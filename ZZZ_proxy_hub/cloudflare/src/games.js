/* 多遊戲設定（對應 server/games/__init__.py）。
 * games.json 是部署時由 cloudflare/export_games.py 從 Python 設定檔匯出，角色資料只維護 Python 那一份。
 */
import DATA from "./games.json" with { type: "json" };
import { ApiError } from "./common.js";

export const ALL_GAMES = DATA.games;
export const GAMES = ALL_GAMES;
export const DEFAULT_GAME = Object.keys(GAMES)[0];
export const COMMON_ALIASES = DATA.common_aliases;

export function getGame(key) {
  if (!Object.hasOwn(GAMES, key)) throw new ApiError(404, `不支援的遊戲：${key}`);
  return GAMES[key];
}

export const normName = (n) => (n || "").trim().toLowerCase().replace(/ /g, "").replace(/・/g, "·").replace(/•/g, "·");

export const agentById = (game, cid) => game.agents.find((a) => a.id === cid) || null;

export function agentByName(game, name) {
  const id = game.name_index[normName(name)];
  return id ? agentById(game, id) : null;
}

export const isStandard = (game, channel, name) => (game.gacha.standard[channel] || []).includes((name || "").trim());

/** 前端切換遊戲時需要的所有設定（不含伺服器內部用的欄位） */
export function publicMeta(game) {
  const d = game.disc;
  return {
    id: game.id, short: game.short, name: game.name, name_en: game.name_en,
    accent: game.accent, accent_ink: game.accent_ink, labels: game.labels,
    agents: game.agents, elements: game.elements, specialties: game.specialties,
    channels: game.gacha.channels, featured_channel: game.gacha.featured_channel,
    channel_order: game.gacha.channel_order,
    stat_labels: game.stat_labels,
    disc: { item: d.item, slots: d.slots, default_slot: d.default_slot, sub_stats: Object.keys(d.sub_roll) },
  };
}
