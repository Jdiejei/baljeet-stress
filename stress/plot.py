"""Graphs of a stress run (out/samples.jsonl): players and tick health over time, and resources."""
import json, os, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

out = sys.argv[1]
S = [json.loads(l) for l in open(os.path.join(out, 'samples.jsonl'))]
t = [s['t'] / 60 for s in S]
G = 2 ** 30


def col(k, f=lambda v: v):
    return [f(s[k]) if s.get(k) is not None else None for s in S]


fig, ax = plt.subplots(4, 1, figsize=(12, 13), sharex=True)
ax[0].plot(t, col('online'), color='#2a6fdb', label='players online')
ax[0].plot(t, col('wanted'), color='#2a6fdb', ls=':', label='bots started')
a2 = ax[0].twinx(); a2.plot(t, col('chunks'), color='#888', label='chunks loaded'); a2.set_ylabel('chunks')
ax[0].set_ylabel('players'); ax[0].legend(loc='upper left')
ax[1].plot(t, col('mspt5s'), color='#d9480f', lw=1, label='MSPT (5 s avg)')
ax[1].plot(t, col('mspt5s_max'), color='#d9480f', lw=0.5, alpha=0.4, label='MSPT (5 s max)')
ax[1].axhline(50, color='k', ls='--', lw=0.8); ax[1].text(t[0], 51, '50 ms = lag starts', fontsize=8)
ax[1].set_ylim(0, max(120, max([v for v in col('mspt5s') if v] or [0]) * 1.1)); ax[1].set_ylabel('ms per tick'); ax[1].legend(loc='upper left')
ax[2].plot(t, col('tps1m'), color='#2b8a3e', label='TPS (1 min)'); ax[2].set_ylim(0, 21); ax[2].set_ylabel('TPS'); ax[2].legend(loc='lower left')
ax[3].plot(t, col('java_cpu'), color='#7048e8', label='server CPU %')
ax[3].plot(t, col('bots_cpu'), color='#ae3ec9', ls=':', label='bot clients CPU %')
a3 = ax[3].twinx()
a3.plot(t, col('heap_used', lambda v: v / G), color='#e67700', label='server heap GB')
a3.plot(t, col('mem_avail', lambda v: v / G), color='#495057', ls='--', label='machine RAM free GB'); a3.set_ylabel('GB')
ax[3].set_ylabel('CPU % (400 = all 4 cores)'); ax[3].legend(loc='upper left'); a3.legend(loc='upper right')
ax[3].set_xlabel('minutes')
phases, last = [], None
for s in S:
    if s['phase'] != last: phases.append((s['t'] / 60, s['phase'])); last = s['phase']
for a in ax:
    for x, p in phases: a.axvline(x, color='#bbb', lw=0.6)
for x, p in phases: ax[0].text(x, ax[0].get_ylim()[1] * 0.92, ' ' + p, fontsize=8, color='#666')
fig.tight_layout(); fig.savefig(os.path.join(out, 'timeline.png'), dpi=110)

W = json.load(open(os.path.join(out, 'waves.json'))) if os.path.exists(os.path.join(out, 'waves.json')) else []
if W:
    fig, ax = plt.subplots(figsize=(10, 5))
    b = [w.get('bots', w.get('online')) for w in W]
    ax.plot(b, [w['mspt'] for w in W], 'o-', color='#d9480f', label='MSPT avg')
    ax.plot(b, [w['mspt_peak'] for w in W], 'x:', color='#d9480f', alpha=0.5, label='MSPT peak')
    ax.axhline(50, color='k', ls='--', lw=0.8)
    ax.set_xlabel('player bots'); ax.set_ylabel('ms per tick')
    a2 = ax.twinx(); a2.plot(b, [w['tps'] for w in W], 's-', color='#2b8a3e', label='TPS'); a2.set_ylim(0, 21); a2.set_ylabel('TPS')
    ax.legend(loc='upper left'); a2.legend(loc='lower left')
    fig.tight_layout(); fig.savefig(os.path.join(out, 'capacity.png'), dpi=110)
print('graphs written')
