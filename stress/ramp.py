"""Bot machine side: waits for the server's tunnel address, connects through cloudflared, and adds player bots in
waves until the server says it is done (or this machine's cap). Logs this machine's CPU and memory each wave."""
import os, subprocess, sys, time

RUN, PREFIX = os.environ['GITHUB_RUN_ID'], os.environ['PREFIX']
WAVE, WAVE_S, CAP = int(os.environ.get('WAVE', 15)), int(os.environ.get('WAVE_S', 40)), int(os.environ.get('CAP', 200))
HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs('out', exist_ok=True)


def fetch(name):
    return subprocess.run(['gh', 'release', 'download', 'endpoint', '-p', name, '-O', name, '--clobber'], capture_output=True).returncode == 0


for _ in range(150):                                   # the server machine needs a few minutes to build and start
    if fetch(f'endpoint-{RUN}.txt'): break
    time.sleep(6)
else:
    sys.exit('no endpoint')
host = open(f'endpoint-{RUN}.txt').read().strip()
subprocess.Popen(['./cloudflared', 'access', 'tcp', '--hostname', host, '--url', '127.0.0.1:25565'],
                 stdout=open('out/access.log', 'w'), stderr=subprocess.STDOUT)
time.sleep(5)
print('connected through the tunnel', flush=True)
procs, n = [], 0
env = dict(os.environ, MC_HOST='127.0.0.1', MC_PORT='25565', MC_VD=os.environ.get('VD', '12'))
while n < CAP:
    for i in range(0, WAVE, 25):
        names = [f'{PREFIX}{k:03d}' for k in range(n + i + 1, n + min(i + 25, WAVE) + 1)]
        procs.append(subprocess.Popen(['node', os.path.join(HERE, 'bots.js'), ','.join(names)], env=env,
                                      stdout=open(f'out/bots_{PREFIX}{n + i + 1:03d}.log', 'w'), stderr=subprocess.STDOUT))
    n += WAVE
    time.sleep(WAVE_S)
    load = os.getloadavg()[0]
    avail = int(open('/proc/meminfo').read().split('MemAvailable:')[1].split()[0]) // 1024
    print(f'{time.strftime("%H:%M:%S")} {n} bots started here, load {load:.1f} on {os.cpu_count()} cores, {avail} MB free', flush=True)
    if fetch(f'done-{RUN}.txt'): break
while not fetch(f'done-{RUN}.txt'): time.sleep(10)
for p in procs: p.terminate()
print('server finished', flush=True)
