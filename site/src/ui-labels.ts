import type { Age, Difficulty, Emotion, Gender } from "@/data";

export const GENDER_LABELS = {
  female: "女性",
  male: "男性",
  neutral: "中性",
} as const satisfies Record<Gender, string>;

export const AGE_LABELS = {
  child: "子ども",
  teen: "10代",
  young_adult: "若年",
  adult: "成人",
  middle_aged: "中年",
  elderly: "高齢",
} as const satisfies Record<Age, string>;

export const EMOTION_LABELS = {
  neutral: "平静",
  cheerful: "明るい",
  angry: "怒り",
  sad: "悲しみ",
  fearful: "恐れ",
  surprised: "驚き",
  tired: "疲れ",
  drunk: "酔い",
  whisper: "ささやき",
  shout: "叫び",
  laughing: "笑い",
  pain: "苦痛",
} as const satisfies Record<Emotion, string>;

export const DIFFICULTY_LABELS = {
  standard: "標準",
  hard: "難読",
} as const satisfies Record<Difficulty, string>;
