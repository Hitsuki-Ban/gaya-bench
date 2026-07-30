from __future__ import annotations

from collections.abc import Mapping

# character.reference_voice が null のキャラクターだけを列挙する。
# scenario 側に明示された参照音声は、各 adapter がこの表より優先する。
CLONE_REFERENCE_ASSIGNMENTS: Mapping[tuple[str, str], str] = {
    # battlefield-camp
    ("battlefield-camp", "wounded"): "hadou-emotion-11",
    ("battlefield-camp", "messenger"): "lux-emotion-76",
    ("battlefield-camp", "veteran-soldier"): "hadou-emotion-11",
    ("battlefield-camp", "medic"): "lux-emotion-76",
    # castle-gate
    ("castle-gate", "guard-otoko"): "hadou-emotion-11",
    ("castle-gate", "guard-onna"): "lux-emotion-76",
    ("castle-gate", "captain"): "lux-emotion-76",
    # chinatown-chat
    ("chinatown-chat", "mahjong-inkyo"): "hadou-emotion-11",
    ("chinatown-chat", "chakan-obaa"): "sayoko-emotion-75",
    ("chinatown-chat", "wakate-tenin"): "hadou-emotion-11",
    ("chinatown-chat", "kanpo-shujin"): "hadou-emotion-11",
    # chinatown-street
    ("chinatown-street", "tenshin-okami"): "amitaro-countdown",
    ("chinatown-street", "shokudo-oyaji"): "hadou-emotion-11",
    ("chinatown-street", "nimotsu-ani"): "hadou-emotion-11",
    # clockwork-plaza
    ("clockwork-plaza", "guide-automaton"): "hadou-emotion-11",
    ("clockwork-plaza", "sentry-unit"): "hadou-emotion-11",
    ("clockwork-plaza", "vendor-doll"): "amitaro-countdown",
    ("clockwork-plaza", "fortune-doll"): "tsukuyomi-corpus-94",
    # dungeon-entrance
    ("dungeon-entrance", "vanguard"): "hadou-emotion-11",
    ("dungeon-entrance", "rookie-archer"): "tsukuyomi-corpus-94",
    ("dungeon-entrance", "scout"): "lux-emotion-76",
    ("dungeon-entrance", "old-guide"): "hadou-emotion-11",
    # festival-night
    ("festival-night", "yatai-obasan"): "amitaro-countdown",
    ("festival-night", "matsuri-wakamono"): "hadou-emotion-11",
    ("festival-night", "matsuri-kid"): "tsukuyomi-corpus-94",
    ("festival-night", "mikoshi-katsugite"): "hadou-emotion-11",
    # goblin-camp
    ("goblin-camp", "goblin-lookout"): "tsukuyomi-corpus-94",
    ("goblin-camp", "goblin-cook"): "tsukuyomi-corpus-94",
    ("goblin-camp", "orc-brother"): "hadou-emotion-11",
    ("goblin-camp", "goblin-shaman"): "sayoko-emotion-75",
    # guild-hall
    ("guild-hall", "veteran"): "hadou-emotion-11",
    ("guild-hall", "rookie"): "tsukuyomi-corpus-94",
    ("guild-hall", "weary"): "lux-emotion-76",
    # market-day
    ("market-day", "fruit-vendor"): "hadou-emotion-11",
    ("market-day", "shopper"): "lux-emotion-76",
    ("market-day", "street-kid"): "tsukuyomi-corpus-94",
    # spirit-forest
    ("spirit-forest", "pixie"): "tsukuyomi-corpus-94",
    ("spirit-forest", "elder-tree"): "hadou-emotion-11",
    ("spirit-forest", "wisp"): "lux-emotion-76",
    ("spirit-forest", "spring-sprite"): "lux-emotion-76",
    # tavern-night
    ("tavern-night", "drunkard"): "hadou-emotion-11",
    ("tavern-night", "old-regular"): "hadou-emotion-11",
    # village-morning
    ("village-morning", "teen-boy"): "tsukuyomi-corpus-94",
    ("village-morning", "farm-wife"): "amitaro-countdown",
    ("village-morning", "farmer-man"): "hadou-emotion-11",
    # west-crowd
    ("west-crowd", "isogi-shinshi"): "hadou-emotion-11",
    ("west-crowd", "oshaberi-fujin"): "amitaro-countdown",
    ("west-crowd", "shinbun-shounen"): "tsukuyomi-corpus-94",
    ("west-crowd", "machibouke-seinen"): "hadou-emotion-11",
    # west-market
    ("west-market", "hana-uri"): "lux-emotion-76",
    ("west-market", "cheese-shujin"): "hadou-emotion-11",
    ("west-market", "kyuuji"): "hadou-emotion-11",
    ("west-market", "kankou-fujin"): "lux-emotion-76",
}


# Supertonic 3公式の10 built-in voiceの声質説明を基準に、
# キャラクターごとの選択を実行時推測ではなく明示的に固定する。
SUPERTONIC3_VOICE_ASSIGNMENTS: Mapping[tuple[str, str], str] = {
    # battlefield-camp
    ("battlefield-camp", "wounded"): "M2",
    ("battlefield-camp", "messenger"): "F4",
    ("battlefield-camp", "veteran-soldier"): "M2",
    ("battlefield-camp", "medic"): "F4",
    # castle-gate
    ("castle-gate", "guard-otoko"): "M3",
    ("castle-gate", "guard-onna"): "F3",
    ("castle-gate", "captain"): "F4",
    ("castle-gate", "merchant"): "M1",
    # chinatown-chat
    ("chinatown-chat", "mahjong-inkyo"): "M5",
    ("chinatown-chat", "chakan-obaa"): "F5",
    ("chinatown-chat", "wakate-tenin"): "M4",
    ("chinatown-chat", "kanpo-shujin"): "M2",
    # chinatown-street
    ("chinatown-street", "tenshin-okami"): "F4",
    ("chinatown-street", "shokudo-oyaji"): "M2",
    ("chinatown-street", "kaimono-musume"): "F2",
    ("chinatown-street", "nimotsu-ani"): "M1",
    # clockwork-plaza
    ("clockwork-plaza", "guide-automaton"): "F3",
    ("clockwork-plaza", "sentry-unit"): "M2",
    ("clockwork-plaza", "vendor-doll"): "F2",
    ("clockwork-plaza", "fortune-doll"): "F5",
    # dungeon-entrance
    ("dungeon-entrance", "vanguard"): "M2",
    ("dungeon-entrance", "rookie-archer"): "F2",
    ("dungeon-entrance", "scout"): "F1",
    ("dungeon-entrance", "old-guide"): "M5",
    # festival-night
    ("festival-night", "yatai-obasan"): "F4",
    ("festival-night", "matsuri-wakamono"): "M1",
    ("festival-night", "matsuri-kid"): "F2",
    ("festival-night", "mikoshi-katsugite"): "M2",
    # goblin-camp
    ("goblin-camp", "goblin-lookout"): "M4",
    ("goblin-camp", "goblin-cook"): "F2",
    ("goblin-camp", "orc-brother"): "M2",
    ("goblin-camp", "goblin-shaman"): "M5",
    # guild-hall
    ("guild-hall", "receptionist"): "F3",
    ("guild-hall", "veteran"): "M3",
    ("guild-hall", "rookie"): "M4",
    ("guild-hall", "weary"): "F1",
    # market-day
    ("market-day", "fruit-vendor"): "M1",
    ("market-day", "shopper"): "F1",
    ("market-day", "street-kid"): "F2",
    # spirit-forest
    ("spirit-forest", "pixie"): "F2",
    ("spirit-forest", "elder-tree"): "M2",
    ("spirit-forest", "wisp"): "F5",
    ("spirit-forest", "spring-sprite"): "F5",
    # tavern-night
    ("tavern-night", "barmaid"): "F2",
    ("tavern-night", "drunkard"): "M1",
    ("tavern-night", "old-regular"): "M5",
    # village-morning
    ("village-morning", "granny"): "F5",
    ("village-morning", "teen-boy"): "M4",
    ("village-morning", "farm-wife"): "F5",
    ("village-morning", "farmer-man"): "M2",
    # west-crowd
    ("west-crowd", "isogi-shinshi"): "M3",
    ("west-crowd", "oshaberi-fujin"): "F4",
    ("west-crowd", "shinbun-shounen"): "M4",
    ("west-crowd", "machibouke-seinen"): "M4",
    # west-market
    ("west-market", "hana-uri"): "F5",
    ("west-market", "cheese-shujin"): "M2",
    ("west-market", "kyuuji"): "M4",
    ("west-market", "kankou-fujin"): "F5",
}
