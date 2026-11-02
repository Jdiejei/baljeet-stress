// Player bots: real logged-in clients that keep busy like players do. Each wanders (walks, sprints, turns, jumps
// over things), digs the block in front of it, builds a little, fights mobs near it, swings, sneaks, opens its
// inventory and sometimes chats or runs a command. Usage: node bots.js Bot001,Bot002,...
const mineflayer = require('mineflayer');
const { Vec3 } = require('vec3');
const names = process.argv[2].split(',');
const pick = a => a[Math.floor(Math.random() * a.length)];
const chance = p => Math.random() < p;

function start(name, attempt = 0) {
  const bot = mineflayer.createBot({ host: '127.0.0.1', port: 25565, username: name, version: '1.21.11', auth: 'offline',
                                     viewDistance: 8, checkTimeoutInterval: 90000 });
  let timer = null;
  bot.once('spawn', () => {
    console.log(`${Date.now()} ${name} joined`);
    timer = setInterval(() => act(bot).catch(() => {}), 700 + Math.random() * 600);
  });
  bot.on('death', () => console.log(`${Date.now()} ${name} died`));
  bot.on('kicked', r => console.log(`${Date.now()} ${name} kicked ${JSON.stringify(r).slice(0, 160)}`));
  bot.on('error', e => console.log(`${Date.now()} ${name} error ${e.message}`));
  bot.on('end', reason => {
    clearInterval(timer);
    console.log(`${Date.now()} ${name} left ${reason}`);
    if (attempt < 5) setTimeout(() => start(name, attempt + 1), 5000 + Math.random() * 5000);   // a player rejoins
  });
}

let busy = new WeakSet();
async function act(bot) {
  if (!bot.entity || busy.has(bot)) return;
  busy.add(bot);
  try {
    const r = Math.random();
    if (r < 0.45) await wander(bot);
    else if (r < 0.62) await dig(bot);
    else if (r < 0.72) await build(bot);
    else if (r < 0.84) await fight(bot);
    else if (r < 0.92) { bot.setControlState('sneak', true); await sleep(600); bot.setControlState('sneak', false); }
    else if (r < 0.97) { bot.swingArm(); bot.setQuickBarSlot(Math.floor(Math.random() * 9)); }
    else if (chance(0.5)) bot.chat(pick(['hi', 'anyone at the dig site?', 'gg', 'lol', 'where is the trader', 'brb']));
    else bot.chat(pick(['/souls', '/vault', '/help']));
  } finally { busy.delete(bot); }
}

async function wander(bot) {
  await bot.look(bot.entity.yaw + (Math.random() - 0.5) * 2.2, (Math.random() - 0.5) * 0.5, true);
  bot.setControlState('forward', true);
  bot.setControlState('sprint', chance(0.5));
  if (chance(0.3)) bot.setControlState(pick(['left', 'right']), true);
  for (let i = 0; i < 6; i++) {
    const ahead = bot.blockAt(bot.entity.position.offset(-Math.sin(bot.entity.yaw), 0.5, -Math.cos(bot.entity.yaw)));
    bot.setControlState('jump', !!(ahead && ahead.boundingBox === 'block') || chance(0.1) || bot.entity.isInWater);
    await sleep(350);
  }
  bot.clearControlStates();
}

async function dig(bot) {
  await bot.look(bot.entity.yaw + (Math.random() - 0.5), -0.4 - Math.random() * 0.6, true);
  const b = bot.blockAtCursor(4);
  if (!b || b.name === 'bedrock' || !bot.canDigBlock(b)) return;
  const tool = bot.inventory.items().find(i => i.name.endsWith('pickaxe'));
  if (tool) await bot.equip(tool, 'hand');
  await Promise.race([bot.dig(b, true), sleep(6000)]).catch(() => {});
  bot.stopDigging();
}

async function build(bot) {
  const dirt = bot.inventory.items().find(i => i.name === 'dirt');
  if (!dirt) return;
  await bot.equip(dirt, 'hand');
  const below = bot.blockAt(bot.entity.position.offset(0, -1, 0));
  if (!below || below.boundingBox !== 'block') return;
  // a pillar step: jump and put a block where the feet were
  bot.setControlState('jump', true);
  await sleep(300);
  bot.setControlState('jump', false);
  await Promise.race([bot.placeBlock(below, new Vec3(0, 1, 0)), sleep(1500)]).catch(() => {});
}

async function fight(bot) {
  const mob = bot.nearestEntity(e => e.type === 'hostile' || e.type === 'mob' || e.type === 'animal');
  if (!mob || mob.position.distanceTo(bot.entity.position) > 12) return wander(bot);
  const sword = bot.inventory.items().find(i => i.name.endsWith('sword'));
  if (sword) await bot.equip(sword, 'hand');
  for (let i = 0; i < 6 && mob.isValid; i++) {
    await bot.lookAt(mob.position.offset(0, mob.height * 0.8, 0), true);
    if (mob.position.distanceTo(bot.entity.position) > 3) { bot.setControlState('forward', true); bot.setControlState('sprint', true); }
    else { bot.clearControlStates(); bot.attack(mob); }
    await sleep(600);
  }
  bot.clearControlStates();
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
names.forEach((n, i) => setTimeout(() => start(n), i * 350));
