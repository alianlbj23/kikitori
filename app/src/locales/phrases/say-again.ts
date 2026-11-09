/**
 * The interpret-mode repair phrase (docs/specs/v0.3-features.md G3) in every
 * subtitle language. It is shown to the other person in *their* language,
 * not in the interface language, so it is data rather than a dictionary key;
 * it lives here because every per-language text belongs under src/locales.
 */

/**
 * "Sorry, could you say that again?" in every subtitle language (catalog codes,
 * `kikitori.languages`). Polite, short, and understandable without context.
 */
export const SAY_AGAIN: Readonly<Record<string, string>> = {
  ja: "すみません、もう一度言っていただけますか？",
  en: "Sorry, could you say that again?",
  "zh-TW": "不好意思，可以請你再說一次嗎？",
  "zh-CN": "不好意思，可以请你再说一遍吗？",
  ko: "죄송하지만 다시 한번 말씀해 주시겠어요?",
  es: "Perdón, ¿puede repetirlo, por favor?",
  fr: "Pardon, pouvez-vous répéter, s'il vous plaît ?",
  de: "Entschuldigung, können Sie das bitte wiederholen?",
  pt: "Desculpe, pode repetir, por favor?",
  ru: "Извините, не могли бы вы повторить?",
  it: "Scusi, può ripetere, per favore?",
  ar: "عذرًا، هل يمكنك أن تكرر ذلك من فضلك؟",
  hi: "माफ़ कीजिए, क्या आप फिर से कह सकते हैं?",
  id: "Maaf, bisa diulangi sekali lagi?",
  vi: "Xin lỗi, bạn có thể nói lại một lần nữa không?",
  th: "ขอโทษครับ/ค่ะ ช่วยพูดอีกครั้งได้ไหมครับ/คะ",
  tr: "Pardon, tekrar söyleyebilir misiniz?",
  pl: "Przepraszam, czy może Pan/Pani powtórzyć?",
  nl: "Sorry, kunt u dat nog een keer zeggen?",
  uk: "Перепрошую, чи не могли б ви повторити?",
};
