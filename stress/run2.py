"""Server side of the two-machine test: the server, reachable through a Cloudflare quick tunnel; player bots come in
from the bot machines (stress/ramp.py). Each newcomer is set up and sent to one of the dimensions; the server's tick
health is measured against how many are online until it breaks."""
import json, os, re, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run as R

RUN = os.environ['GITHUB_RUN_ID']
CAP = int(os.environ.get('MAX_PLAYERS') or 400)
STEP_S = 40


def gh(*a):
    return subprocess.run(['gh', *a], capture_output=True, text=True)


def publish(name, text):
    open(name, 'w').write(text)
    if gh('release', 'view', 'endpoint').returncode != 0:
        gh('release', 'create', 'endpoint', '--title', 'endpoint', '--notes', 'test endpoints', '--latest=false')
    r = gh('release', 'upload', 'endpoint', name, '--clobber')
    R.log(f'published {name}' + ('' if r.returncode == 0 else ' FAILED ' + r.stderr[:200]))


def tunnel():
    logf = os.path.join(R.OUT, 'tunnel.log')
    subprocess.Popen(['./cloudflared', 'tunnel', '--no-autoupdate', '--url', 'tcp://localhost:25565'], stdout=open(logf, 'w'), stderr=subprocess.STDOUT)
    for _ in range(90):
        time.sleep(2)
        m = re.search(r'https://([a-z0-9-]+\.trycloudflare\.com)', open(logf).read())
        if m:
            R.log('tunnel up')
            return m.group(1)
    sys.exit('no tunnel')


seen = []


def welcome(rc, radius):
    """Every new player: survival, can't die, a pickaxe, sword and dirt, then spread into its dimension's generated ground."""
    while not R.state.get('stop'):
        out = rc('list')
        names = [n.strip() for n in out.split(':', 1)[1].split(',')] if ':' in out else []
        for name in names:
            if not name or name in seen: continue
            seen.append(name)
            key, world, _, under = R.DIMS[len(seen) % len(R.DIMS)]
            r = max(48, min(radius[world] - 48, 900))
            for c in ('gamemode survival {n}', 'effect give {n} resistance infinite 4 true', 'effect give {n} saturation infinite 0 true',
                      'effect give {n} fire_resistance infinite 0 true', 'effect give {n} water_breathing infinite 0 true',
                      'give {n} stone_pickaxe', 'give {n} dirt 64', 'give {n} stone_sword'):
                rc(c.format(n=name))
            u = f' under {under}' if under else ''
            o = rc(f'execute in {key} run spreadplayers 0 0 16 {r}{u} false {name}')
            if 'Could not' in o or 'ERR' in o: rc(f'execute in {key} run spreadplayers 0 0 8 {min(r, 200)}{u} false {name}')
        time.sleep(2)


def main():
    R.setup()
    java = R.start_server()
    rc = R.Rcon()
    threading.Thread(target=R.sampler, args=(rc, java), daemon=True).start()
    radius = R.pregen(rc)
    host = tunnel()
    threading.Thread(target=welcome, args=(rc, radius), daemon=True).start()
    publish(f'endpoint-{RUN}.txt', host)
    R.state['phase'] = 'players'
    steps, strained, broken, bad, flat = [], None, None, 0, 0
    t_end = time.time() + 30 * 60
    while time.time() < t_end and not R.state.get('dead'):
        time.sleep(STEP_S)
        w = R.window(STEP_S * 0.75)
        online = R.samples[-1]['online'] if R.samples else 0
        R.state['bots_wanted'] = len(seen)
        if online <= 0 and not steps: continue
        m = R.mean(s['mspt5s'] for s in w)
        st = {'online': online, 'mspt': m, 'mspt_peak': max((s['mspt5s_max'] or 0) for s in w) if w else None,
              'tps': R.mean(s['tps1m'] for s in w), 'heap': R.mean(s['heap_used'] for s in w), 'joined_total': len(seen)}
        steps.append(st)
        json.dump(steps, open(os.path.join(R.OUT, 'waves.json'), 'w'), indent=1)
        R.log(f'{online} online: MSPT {m or 0:.1f} (peak {st["mspt_peak"] or 0:.0f}), heap {(st["heap"] or 0) / 2**30:.1f} GB', **st)
        if strained is None and m and m > 45: strained = online; R.log(f'first strain at {online} players', strained=online)
        bad = bad + 1 if (m or 999) > 75 else 0
        flat = flat + 1 if len(steps) > 1 and online <= steps[-2]['online'] else 0
        if bad >= 2: broken = online; R.log(f'broken at {online} players', broken=online); break
        if online >= CAP or flat >= 4: R.log('stopped: ' + ('cap reached' if online >= CAP else 'player count stopped growing (bot machines full?)')); break
    publish(f'done-{RUN}.txt', 'done')
    R.state['phase'] = 'cooldown'
    good = [s['online'] for s in steps if s['mspt'] is not None and s['mspt'] <= 45]
    summary = {'machine': open(os.path.join(R.OUT, 'machine.txt')).read().split('\n'), 'steps': steps, 'smooth_max': max(good) if good else 0,
               'first_strain': strained, 'broken': broken}
    json.dump(summary, open(os.path.join(R.OUT, 'summary.json'), 'w'), indent=1)
    md = ['## Result (bots on separate machines)', f'- most players with smooth ticks (MSPT <= 45): **{summary["smooth_max"]}**',
          f'- first strain: **{strained}**', f'- broke at: **{broken}**', '', '| online | MSPT | peak | heap GB |', '|---|---|---|---|']
    md += [f"| {s['online']} | {s['mspt'] or 0:.1f} | {s['mspt_peak'] or 0:.0f} | {(s['heap'] or 0) / 2**30:.1f} |" for s in steps]
    open(os.path.join(R.OUT, 'summary.md'), 'w').write('\n'.join(md) + '\n')
    time.sleep(10)
    R.state['stop'] = True
    rc('stop')
    try: java.wait(120)
    except Exception: java.kill()


if __name__ == '__main__':
    main()
