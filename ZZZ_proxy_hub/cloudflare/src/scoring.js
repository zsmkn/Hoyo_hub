/* 裝備評分（驅動盤 / 聖遺物 / 遺器）：以「有效詞條數」計分（對應 server/scoring.py） */
import { ApiError } from "./common.js";
import { agentById } from "./games.js";

function grade(eff) {
  for (const [th, g] of [[7, "SSS"], [6, "SS"], [5, "S"], [4, "A"], [3, "B"]]) if (eff >= th) return g;
  return "C";
}

function buildOf(game, agent) {
  const d = game.disc;
  if (agent && agent.build) return agent.build;
  if (agent && d.specialty_build && d.specialty_build[agent.specialty]) return d.specialty_build[agent.specialty];
  if (agent && d.builds[agent.specialty]) return agent.specialty;
  return d.default_build || Object.keys(d.builds)[0];
}

export function score(game, character, slot, main, subs) {
  const d = game.disc, labels = game.stat_labels;
  subs = subs || [];
  if (!Array.isArray(subs)) throw new ApiError(400, "subs 必須是陣列");
  if (subs.length > 4) throw new ApiError(400, "副詞條最多 4 條");
  const agent = agentById(game, character);
  const build = buildOf(game, agent);
  const weights = (character && d.char_weights[character]) || d.builds[build].weights;

  const rows = [];
  let effTotal = 0;
  for (const s of subs) {
    const stat = s && s.stat, value = Number((s && s.value) || 0);
    if (!Object.hasOwn(d.sub_roll, stat) || !Number.isFinite(value)) throw new ApiError(400, `不支援的副詞條：${stat}`);
    const rolls = value / d.sub_roll[stat];
    const w = weights[stat] || 0;
    effTotal += rolls * w;
    rows.push({ stat, label: labels[stat], value, rolls: Math.round(rolls * 100) / 100, weight: w, effective: Math.round(rolls * w * 100) / 100 });
  }

  slot = Number.parseInt(slot, 10) || null;
  const slotInfo = d.slots.find((s) => s.id === slot);
  const slotLabel = slotInfo ? slotInfo.label : `${slot} 號位`;
  const advice = [];
  const mainStat = main && main.stat;
  const rec = d.builds[build].mains[slot];
  if (rec && mainStat) {
    if (rec.includes(mainStat)) advice.push(`${slotLabel}主詞條「${labels[mainStat] || mainStat}」✔ 符合推薦`);
    else advice.push(`${slotLabel}建議主詞條：${rec.map((r) => labels[r]).join("、")}（目前是 ${labels[mainStat] || mainStat}）`);
  }
  const useless = rows.filter((r) => r.weight === 0).map((r) => r.label);
  if (useless.length) advice.push("無效副詞條：" + useless.join("、"));

  effTotal = Math.round(effTotal * 100) / 100;
  return {
    character: agent ? agent.name : null, build, slot, main: main || null,
    subs: rows, effective_rolls: effTotal,
    score: Math.round(Math.min(effTotal / d.max_rolls, 1) * 1000) / 10, grade: grade(effTotal),
    weights: Object.fromEntries(Object.entries(weights).map(([k, v]) => [labels[k], v])), advice,
  };
}
