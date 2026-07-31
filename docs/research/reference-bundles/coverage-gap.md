# 58 役 × 現 5 bundle の coverage gap

基準日: 2026-07-31
機械可読正本: [`coverage-gap.json`](coverage-gap.json)

## 基準

scenario 15 件・character 58 件を `main@19a435b` から抽出し、現 5 素材の知覚属性と
比較した。assignment は #177 で承認され #174 release に統合中の male 4 役修正を
適用した状態である。したがって、main の誤割当そのものと修正後の coverage を
混同しない。

`coverage-gap.json` の `role_truth` は 58 役すべてについて scenario YAML の
`kind`、`gender`、`age`、`archetype`、`voice`、`personality` を機械的に転記した
調達判断用の正本である。

- `gender exact`: binary gender が bundle と一致する。
- `neutral unsupported`: 現 5 bundle に neutral voice がなく、近い binary 声を
  exact と数えない。
- `age approximate`: age が一致しない。隣接年齢かどうかを理由に exact にしない。
- `kind exact`: scenario の `kind` が未指定、すなわち human。
- `kind mismatch`: machine / creature / spirit に人声 bundle を割り当てている。
- `all exact`: gender、age、kind がすべて exact。

## 集計

| 軸 | exact | approximate / unsupported / mismatch |
| --- | ---: | ---: |
| gender | 51 | neutral unsupported 7 / binary mismatch 0 |
| age | 21 | approximate 37 |
| gender + age | 18 | gap 40 |
| kind | 46 | mismatch 12 |
| 全属性 | 16 | gap 42 |

`kind` の内訳は machine 4、creature 4、spirit 4。gender は male 28、female 23、
neutral 7。age は adult 12、young_adult 16、middle_aged 13、elderly 7、teen 5、
child 5 である。

以下は 58 役を重複なしで 5 群に分けた一覧である。`†` は scenario YAML に
`reference_voice` が明示された 5 件、それ以外は全量 assignment table 由来。

## A. 全属性 exact（16）

| role | bundle | role | bundle |
| --- | --- | --- | --- |
| `battlefield-camp/wounded` | `hadou-emotion-11` | `battlefield-camp/messenger` | `lux-emotion-76` |
| `castle-gate/guard-onna` | `lux-emotion-76` | `castle-gate/merchant`† | `hadou-emotion-11` |
| `chinatown-chat/chakan-obaa` | `sayoko-emotion-75` | `chinatown-street/kaimono-musume`† | `tsukuyomi-corpus-94` |
| `dungeon-entrance/vanguard` | `hadou-emotion-11` | `dungeon-entrance/rookie-archer` | `tsukuyomi-corpus-94` |
| `dungeon-entrance/scout` | `lux-emotion-76` | `guild-hall/receptionist`† | `lux-emotion-76` |
| `guild-hall/veteran` | `hadou-emotion-11` | `market-day/fruit-vendor` | `hadou-emotion-11` |
| `tavern-night/barmaid`† | `amitaro-countdown` | `village-morning/granny`† | `sayoko-emotion-75` |
| `west-crowd/isogi-shinshi` | `hadou-emotion-11` | `west-market/hana-uri` | `lux-emotion-76` |

## B. human / binary gender exact / age approximate（27）

| role | bundle | role | bundle |
| --- | --- | --- | --- |
| `battlefield-camp/veteran-soldier` | `hadou-emotion-11` | `battlefield-camp/medic` | `lux-emotion-76` |
| `castle-gate/guard-otoko` | `hadou-emotion-11` | `castle-gate/captain` | `lux-emotion-76` |
| `chinatown-chat/mahjong-inkyo` | `hadou-emotion-11` | `chinatown-chat/wakate-tenin` | `hadou-emotion-11` |
| `chinatown-chat/kanpo-shujin` | `hadou-emotion-11` | `chinatown-street/tenshin-okami` | `amitaro-countdown` |
| `chinatown-street/shokudo-oyaji` | `hadou-emotion-11` | `chinatown-street/nimotsu-ani` | `hadou-emotion-11` |
| `dungeon-entrance/old-guide` | `hadou-emotion-11` | `festival-night/yatai-obasan` | `amitaro-countdown` |
| `festival-night/matsuri-wakamono` | `hadou-emotion-11` | `festival-night/mikoshi-katsugite` | `hadou-emotion-11` |
| `guild-hall/rookie` | `hadou-emotion-11` | `guild-hall/weary` | `lux-emotion-76` |
| `market-day/shopper` | `lux-emotion-76` | `tavern-night/drunkard` | `hadou-emotion-11` |
| `tavern-night/old-regular` | `hadou-emotion-11` | `village-morning/teen-boy` | `hadou-emotion-11` |
| `village-morning/farm-wife` | `amitaro-countdown` | `village-morning/farmer-man` | `hadou-emotion-11` |
| `west-crowd/oshaberi-fujin` | `amitaro-countdown` | `west-crowd/shinbun-shounen` | `hadou-emotion-11` |
| `west-crowd/machibouke-seinen` | `hadou-emotion-11` | `west-market/cheese-shujin` | `hadou-emotion-11` |
| `west-market/kankou-fujin` | `lux-emotion-76` |  |  |

## C. human / neutral unsupported（3）

| role | bundle | age |
| --- | --- | --- |
| `festival-night/matsuri-kid` | `tsukuyomi-corpus-94` | child / approximate |
| `market-day/street-kid` | `tsukuyomi-corpus-94` | child / approximate |
| `west-market/kyuuji` | `hadou-emotion-11` | young_adult / approximate |

## D. nonhuman / binary gender exact / kind mismatch（8）

| role | bundle | kind / age status |
| --- | --- | --- |
| `clockwork-plaza/sentry-unit` | `hadou-emotion-11` | machine / approximate |
| `clockwork-plaza/vendor-doll` | `amitaro-countdown` | machine / exact |
| `clockwork-plaza/fortune-doll` | `tsukuyomi-corpus-94` | machine / approximate |
| `goblin-camp/goblin-lookout` | `hadou-emotion-11` | creature / approximate |
| `goblin-camp/goblin-cook` | `tsukuyomi-corpus-94` | creature / approximate |
| `goblin-camp/orc-brother` | `hadou-emotion-11` | creature / approximate |
| `spirit-forest/elder-tree` | `hadou-emotion-11` | spirit / approximate |
| `spirit-forest/spring-sprite` | `lux-emotion-76` | spirit / exact |

## E. nonhuman / neutral unsupported / kind mismatch（4）

| role | bundle | kind / age status |
| --- | --- | --- |
| `clockwork-plaza/guide-automaton` | `hadou-emotion-11` | machine / exact |
| `goblin-camp/goblin-shaman` | `sayoko-emotion-75` | creature / exact |
| `spirit-forest/pixie` | `tsukuyomi-corpus-94` | spirit / approximate |
| `spirit-forest/wisp` | `lux-emotion-76` | spirit / exact |

## main の 4 誤割当と修正

main の clone 共通 table は次の 4 役を female / teen の
`tsukuyomi-corpus-94` へ割り当てていた。#177 の監査で 12 line への影響が確定し、
#174 release 経路では male / adult の `hadou-emotion-11` へ一括修正する。

| role | main | #177/#174 |
| --- | --- | --- |
| `goblin-camp/goblin-lookout` | `tsukuyomi-corpus-94` | `hadou-emotion-11` |
| `guild-hall/rookie` | `tsukuyomi-corpus-94` | `hadou-emotion-11` |
| `village-morning/teen-boy` | `tsukuyomi-corpus-94` | `hadou-emotion-11` |
| `west-crowd/shinbun-shounen` | `tsukuyomi-corpus-94` | `hadou-emotion-11` |

修正前は gender exact 47 / binary mismatch 4 / age exact 24。修正後は gender exact
51 / binary mismatch 0 / age exact 21 となる。teen / child を adult へ移すため
age exact は減るが、binary gender mismatch を age exact と引き換えに残さない。

## 調達仕分け

| 優先 gap | 公開コーパス | whitelist 合成 | 契約実録 |
| --- | --- | --- | --- |
| elderly male | 一次規約を満たす候補を継続調査 | 補助候補 | 実年齢質感が必要なら将来 option |
| child / teen male、neutral child | 本人児童収録を前提にしない | Qwen VoiceDesign → Base clone を優先候補 | 成人 actor の child persona。別 Issue |
| machine / creature / spirit | 人間コーパスを kind exact としない | Qwen fictional persona を優先候補 | 成人 actor の nonhuman persona。別 Issue |

Phase 2 は公開コーパスと whitelist 合成まで。契約実録の実施は Owner 判断の別 Issue
とし、Phase 2 の完了条件へ暗黙追加しない。
