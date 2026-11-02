"""The stress test: start the server, pre-generate and force-load every chunk in each dimension, then ramp up moving
player bots across the dimensions in waves until the server strains, sampling TPS, MSPT, CPU, memory and GC."""
import json, os, re, secrets, socket, struct, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV, OUT = os.path.join(ROOT, 'srv'), os.path.join(ROOT, 'out')
MAX = int(os.environ.get('MAX_PLAYERS') or 300)
WAVE = int(os.environ.get('WAVE') or 10)
WAVE_S = int(float(os.environ.get('WAVE_MINUTES') or 3) * 60)
PER_PROC = 25                                   # bots per node process
PASS = secrets.token_hex(16)
START = time.time()
# (dimension key, Bukkit world name, chunky radius cap, spreadplayers "under" height or None)
DIMS = [('minecraft:vault_map', 'vault_map', 2000, None),
        ('the_sift:the_sift', 'world_the_sift_the_sift', 2000, 150),
        ('minecraft:the_nether', 'world_nether', 1000, 120),
        ('minecraft:the_end', 'world_the_end', 1000, None)]
os.makedirs(OUT, exist_ok=True)
events = []


def log(msg, **kv):
    t = round(time.time() - START)
    print(f'[{t // 60:3d}:{t % 60:02d}] {msg}', flush=True)
    events.append({'t': t, 'msg': msg, **kv})
    json.dump(events, open(os.path.join(OUT, 'events.json'), 'w'), indent=1)


# ------------------------------------------------------------------ RCON
class Rcon:
    def __init__(self):
        self.lock = threading.Lock()
        self.connect()

    def connect(self):
        self.s = socket.create_connection(('127.0.0.1', 25599), timeout=120)
        self._send(3, PASS)

    def _send(self, kind, body):
        data = struct.pack('<ii', 1, kind) + body.encode() + b'\0\0'
        self.s.sendall(struct.pack('<i', len(data)) + data)
        n = struct.unpack('<i', self._read(4))[0]
        _, _ = struct.unpack('<ii', self._read(8))
        return self._read(n - 8)[:-2].decode('utf-8', 'replace')

    def _read(self, n):
        b = b''
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c: raise ConnectionError('rcon closed')
            b += c
        return b

    def __call__(self, cmd):
        with self.lock:
            try:
                return re.sub('§.', '', self._send(2, cmd))
            except Exception:
                try:
                    self.connect()
                    return re.sub('§.', '', self._send(2, cmd))
                except Exception as ex:
                    return f'ERR {ex}'


# ------------------------------------------------------------------ server
def setup():
    p = os.path.join(SRV, 'server.properties')
    props = dict(l.split('=', 1) for l in open(p).read().splitlines() if '=' in l and not l.startswith('#'))
    props.update({'online-mode': 'false', 'enforce-whitelist': 'false', 'white-list': 'false', 'max-players': '1000',
                  'enable-rcon': 'true', 'rcon.port': '25599', 'rcon.password': PASS, 'broadcast-rcon-to-ops': 'false',
                  'spawn-protection': '0', 'allow-flight': 'true', 'player-idle-timeout': '0', 'server-port': '25565'})
    open(p, 'w').write('\n'.join(f'{k}={v}' for k, v in props.items()) + '\n')
    open(os.path.join(SRV, 'eula.txt'), 'w').write('eula=true\n')
    b = os.path.join(SRV, 'bukkit.yml')
    open(b, 'w').write(re.sub(r'connection-throttle: *-?\d+', 'connection-throttle: -1', open(b).read()))
    c = os.path.join(SRV, 'plugins/VaultSMP/config.yml')
    s = open(c).read()
    s = re.sub(r'(resource-pack:(?:\n  .*)*?\n  required:) *true', r'\1 false', s)   # bots don't take resource packs
    open(c, 'w').write(s)
    log('server.properties: view-distance ' + props.get('view-distance', '?') + ', simulation-distance ' + props.get('simulation-distance', '?'),
        view=props.get('view-distance'), sim=props.get('simulation-distance'))


def start_server():
    heap = '11G'
    args = ['java', f'-Xms{heap}', f'-Xmx{heap}', '-XX:+UseG1GC', '-XX:+ParallelRefProcEnabled', '-XX:MaxGCPauseMillis=200',
            '-XX:+UnlockExperimentalVMOptions', '-XX:+DisableExplicitGC', '-XX:G1NewSizePercent=30', '-XX:G1MaxNewSizePercent=40',
            '-XX:G1HeapRegionSize=8M', '-XX:G1ReservePercent=20', '-XX:InitiatingHeapOccupancyPercent=15',
            '-Xlog:gc:file=gc.log:uptime', '-jar', 'paper.jar', '--nogui']
    log(f'starting Paper, heap {heap} (Aikar G1 flags)', heap=heap)
    proc = subprocess.Popen(args, cwd=SRV, stdin=subprocess.PIPE, stdout=open(os.path.join(OUT, 'console.log'), 'w'), stderr=subprocess.STDOUT)
    while True:
        time.sleep(3)
        if proc.poll() is not None: sys.exit('server exited during start')
        txt = open(os.path.join(OUT, 'console.log'), errors='replace').read()
        if re.search(r'Done \([\d.]+s\)!', txt):
            log('server up: ' + re.search(r'Done \(([\d.]+s)\)', txt).group(1))
            return proc
        if time.time() - START > 1800: sys.exit('server never finished starting')


# ------------------------------------------------------------------ sampling
samples = []
state = {'phase': 'idle', 'bots_wanted': 0, 'bots_online': 0}
bot_procs = []


def num(s, default=None):
    m = re.findall(r'[\d.]+', s or '')
    return float(m[0]) if m else default


def ps(pid):
    try:
        out = subprocess.run(['ps', '-o', '%cpu=,rss=', '-p', str(pid)], capture_output=True, text=True).stdout.split()
        return float(out[0]), int(out[1]) * 1024
    except Exception:
        return 0.0, 0


def sampler(rc, java):
    jstat_prev = None
    while not state.get('stop'):
        t0 = time.time()
        tps = rc('tps')
        mspt = rc('mspt')
        lst = rc('list')
        tps_v = [float(x) for x in re.findall(r'\*?(\d+\.\d+)', tps)[:3]] or [None]
        ms = re.findall(r'(\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+)', mspt)          # avg/min/max for 5s, 10s, 1m
        online = int(num(re.search(r'There are (\d+)', lst).group(1)) if re.search(r'There are (\d+)', lst) else -1)
        jcpu, jrss = ps(java.pid)
        bcpu = sum(ps(p.pid)[0] for p in bot_procs if p.poll() is None)
        brss = sum(ps(p.pid)[1] for p in bot_procs if p.poll() is None)
        heap_used = gc_time = None
        try:
            j = subprocess.run(['jstat', '-gc', str(java.pid)], capture_output=True, text=True, timeout=10).stdout.split('\n')
            h, v = j[0].split(), [float(x) for x in j[1].split()]
            d = dict(zip(h, v))
            heap_used = (d['S0U'] + d['S1U'] + d['EU'] + d['OU']) * 1024
            gc_time = d['GCT']
        except Exception:
            pass
        load = os.getloadavg()[0]
        mem = dict((l.split(':')[0], int(l.split()[1]) * 1024) for l in open('/proc/meminfo'))
        s = {'t': round(time.time() - START, 1), 'phase': state['phase'], 'wanted': state['bots_wanted'], 'online': online,
             'tps1m': tps_v[0], 'mspt5s': float(ms[0][0]) if ms else None, 'mspt5s_max': float(ms[0][2]) if ms else None,
             'mspt1m': float(ms[2][0]) if len(ms) > 2 else None, 'java_cpu': jcpu, 'java_rss': jrss, 'bots_cpu': bcpu, 'bots_rss': brss,
             'heap_used': heap_used, 'gc_total_s': gc_time, 'load1': load, 'mem_avail': mem.get('MemAvailable'),
             'chunks': state.get('chunks')}
        samples.append(s)
        with open(os.path.join(OUT, 'samples.jsonl'), 'a') as f: f.write(json.dumps(s) + '\n')
        if java.poll() is not None:
            log('SERVER PROCESS DIED'); state['dead'] = True; return
        time.sleep(max(0.5, 5 - (time.time() - t0)))


def chunk_count(rc):
    total = 0
    for _, world, _, _ in DIMS:
        out = rc(f'paper chunkinfo {world}')
        m = re.search(r'Chunks in [^:]*: *(\d+)', out) or re.search(r'Total.*?: *(\d+)', out)
        if m: total += int(m.group(1))
    state['chunks'] = total
    return total


def window(seconds):
    now = time.time() - START
    return [s for s in samples if s['t'] >= now - seconds and s['mspt5s'] is not None]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ------------------------------------------------------------------ phases
def pregen(rc):
    state['phase'] = 'pregen'
    radius = {}
    for key, world, cap, _ in DIMS:
        w = num(rc(f'execute in {key} run worldborder get'), 60_000_000)
        radius[world] = int(min(w / 2 - 16, cap))
        rc(f'chunky world {world}'); rc('chunky shape square'); rc('chunky center 0 0'); rc(f'chunky radius {radius[world]}')
        rc('chunky start')
        log(f'pre-generating {world}: border {int(w)} wide, radius {radius[world]}', world=world, radius=radius[world])
    deadline = time.time() + 2 * 60
    while time.time() < deadline:
        time.sleep(20)
        txt = open(os.path.join(OUT, 'console.log'), errors='replace').read()
        done = [w for w in radius if re.search(rf'Task finished for {re.escape(w)}', txt)]
        if len(done) == len(radius): break
    else:
        rc('chunky cancel'); log('pre-generation capped at 2 min, cancelled the rest')
    log('pre-generation done')
    return radius


def forceload_all(rc, radius):
    """Every pre-generated chunk force-loaded, 16x16 chunks a command, dimensions interleaved; stops if memory or MSPT give out."""
    state['phase'] = 'forceload'
    jobs = []
    for key, world, _, _ in DIMS:
        r = radius[world] // 16
        jobs.append([(key, cx, cz) for cx in range(-r, r + 1, 16) for cz in range(-r, r + 1, 16)])
    order = [j for group in zip(*[iter_pad(j, max(map(len, jobs))) for j in jobs]) for j in group if j]
    log(f'force-loading {sum(map(len, jobs))} regions of up to 256 chunks across {len(DIMS)} dimensions')
    until = time.time() + 180
    for i, (key, cx, cz) in enumerate(order):
        if time.time() > until:
            log('force-load stopped: 3 minute cap', reason=None); break
        rc(f'execute in {key} run forceload add {cx * 16} {cz * 16} {min(cx + 15, radius_of(key, radius) // 16) * 16} {min(cz + 15, radius_of(key, radius) // 16) * 16}')
        if i % 20 == 19:
            n = chunk_count(rc)
            w = window(15)
            m, avail = mean(s['mspt5s'] for s in w), samples[-1]['mem_avail'] if samples else None
            heap = samples[-1]['heap_used'] if samples else None
            if (heap and heap > 0.92 * 11 * 2 ** 30) or (avail and avail < 1.2 * 2 ** 30) or state.get('dead'):
                log(f'force-load stopped: memory full at {n} chunks', chunks=n, reason='memory'); break
            if m and m > 250:
                log(f'force-load stopped: MSPT {m:.0f} at {n} chunks', chunks=n, reason='mspt'); break
    time.sleep(20)
    n = chunk_count(rc)
    w = window(15)
    log(f'all chunks loaded: {n} chunks, idle MSPT {mean(s["mspt5s"] for s in w) or 0:.1f}, TPS {mean(s["tps1m"] for s in w) or 0:.2f}',
        chunks=n, mspt=mean(s['mspt5s'] for s in w), tps=mean(s['tps1m'] for s in w))
    return n


def iter_pad(lst, n):
    return lst + [None] * (n - len(lst))


def radius_of(key, radius):
    return next(radius[w] for k, w, _, _ in DIMS if k == key)


def spawn_wave(rc, first, count, radius):
    names = [f'Bot{i:03d}' for i in range(first, first + count)]
    for i in range(0, count, PER_PROC):
        part = names[i:i + PER_PROC]
        bot_procs.append(subprocess.Popen(['node', os.path.join(ROOT, 'stress', 'bots.js'), ','.join(part)],
                                          stdout=open(os.path.join(OUT, f'bots_{first + i:03d}.log'), 'w'), stderr=subprocess.STDOUT))
    time.sleep(4 + count * 0.4)
    for n, name in enumerate(names):
        key, world, _, under = DIMS[(first + n) % len(DIMS)]
        r = max(64, min(radius[world] - 32, 900))
        rc(f'gamemode survival {name}')
        rc(f'effect give {name} resistance infinite 4 true'); rc(f'effect give {name} saturation infinite 0 true')
        rc(f'effect give {name} fire_resistance infinite 0 true'); rc(f'effect give {name} water_breathing infinite 0 true')
        rc(f'give {name} stone_pickaxe'); rc(f'give {name} dirt 64'); rc(f'give {name} stone_sword')
        u = f' under {under}' if under else ''
        out = rc(f'execute in {key} run spreadplayers 0 0 16 {r}{u} false {name}')
        if 'Could not' in out or 'ERR' in out:
            rc(f'execute in {key} run spreadplayers 0 0 8 {min(r, 200)}{u} false {name}')


def ramp(rc, radius):
    state['phase'] = 'players'
    strained = broken = None
    waves = []
    n = 0
    while n < MAX and not state.get('dead'):
        spawn_wave(rc, n + 1, WAVE, radius)
        n += WAVE
        state['bots_wanted'] = n
        time.sleep(WAVE_S)
        w = window(WAVE_S * 0.66)
        m = mean(s['mspt5s'] for s in w); peak = max((s['mspt5s_max'] or 0) for s in w) if w else None
        tps = mean(s['tps1m'] for s in w); online = samples[-1]['online'] if samples else -1
        cpu = mean(s['java_cpu'] for s in w); bcpu = mean(s['bots_cpu'] for s in w)
        wv = {'bots': n, 'online': online, 'mspt': m, 'mspt_peak': peak, 'tps': tps, 'java_cpu': cpu, 'bots_cpu': bcpu,
              'heap': mean(s['heap_used'] for s in w), 'mem_avail': samples[-1]['mem_avail'] if samples else None}
        waves.append(wv)
        json.dump(waves, open(os.path.join(OUT, 'waves.json'), 'w'), indent=1)
        log(f'{n} bots ({online} online): MSPT {m or 0:.1f} (peak {peak or 0:.0f}), TPS {tps or 0:.2f}, server CPU {cpu or 0:.0f}%, bots CPU {bcpu or 0:.0f}%', **wv)
        if strained is None and (m is None or m > 45 or (tps or 0) < 19.5):
            strained = n; log(f'first strain at {n} bots', strained=n)
        if m is None or m > 75 or (tps or 0) < 15 or online < n * 0.8:
            broken = n; log(f'broken at {n} bots', broken=n); break
        if (samples[-1]['mem_avail'] or 1e12) < 0.8 * 2 ** 30:
            broken = n; log(f'machine out of memory at {n} bots', broken=n); break
        if strained is not None and n >= strained + 2 * WAVE: break
    return waves, strained, broken


def main():
    setup()
    java = start_server()
    rc = Rcon()
    threading.Thread(target=sampler, args=(rc, java), daemon=True).start()
    time.sleep(5)
    radius = pregen(rc)
    chunks = forceload_all(rc, radius)
    w = window(15)
    if any(e.get('reason') for e in events) or (mean(s['mspt5s'] for s in w) or 0) > 45:
        # the chunks alone already used the server up: players are measured without them (normal loading)
        for key, _, _, _ in DIMS: rc(f'execute in {key} run forceload remove all')
        time.sleep(20)
        log(f'force-loads removed for the player test ({chunk_count(rc)} chunks still loaded)', unforced=True)
    waves, strained, broken = ramp(rc, radius)
    state['phase'] = 'cooldown'
    for p in bot_procs: p.terminate()
    time.sleep(20)
    summary = {'machine': open(os.path.join(OUT, 'machine.txt')).read().split('\n'), 'radius': radius, 'chunks_loaded': chunks,
               'first_strain_bots': strained, 'broken_bots': broken, 'waves': waves,
               'build': open(os.path.join(OUT, 'build_status.txt')).read().strip() if os.path.exists(os.path.join(OUT, 'build_status.txt')) else '?'}
    json.dump(summary, open(os.path.join(OUT, 'summary.json'), 'w'), indent=1)
    good = [w['bots'] for w in waves if w['mspt'] is not None and w['mspt'] <= 45 and (w['tps'] or 0) >= 19.5]
    md = ['## Result', f'- chunks force-loaded at once: **{chunks}**', f'- most bots with a smooth 20 TPS (MSPT <= 45): **{max(good) if good else 0}**',
          f'- first strain: **{strained}** bots', f'- broke at: **{broken}** bots', '', '| bots | online | MSPT | peak | TPS | server CPU % | bots CPU % |', '|---|---|---|---|---|---|---|']
    md += [f"| {w['bots']} | {w['online']} | {w['mspt'] or 0:.1f} | {w['mspt_peak'] or 0:.0f} | {w['tps'] or 0:.2f} | {w['java_cpu'] or 0:.0f} | {w['bots_cpu'] or 0:.0f} |" for w in waves]
    open(os.path.join(OUT, 'summary.md'), 'w').write('\n'.join(md) + '\n')
    log('finished')
    state['stop'] = True
    rc('stop')
    try: java.wait(120)
    except Exception: java.kill()


if __name__ == '__main__':
    main()
