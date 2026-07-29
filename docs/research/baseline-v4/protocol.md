# 公開 baseline v4 release candidate protocol

## 目的

公開中のformat v3 381 clip groupを、新しいN-take harnessでN=1再生成し、
旧公開音声との比較、人間rubric、全group decisionを経てformat v4 release
candidateを作る。Issue #104では公開中の`data/manifest.json`とR2 objectを
変更しない。cutoverは固定したrelease candidateだけを入力に別ticketで行う。

## 固定する対象

- source: raw `data/manifest.json` bytesのSHA-256
- universe: v3 `clips`の381 unique `(model, scenario, line, variant)`
- models: v3に記録された7 model IDのexact集合と、plan生成時のcurrent adapter
  profile metadata
- excluded: v3 `failures`にだけ存在する過去の1 group
- generation: modelごとにN=1、seed base 104

旧clipには現行sidecar/ledgerが必要とするseed、generation input SHA、take id、
toolchain provenanceが存在しない。これらを推定または補完せず、旧Opus pathと
raw SHAだけをreference authorityにする。

旧v3 manifestのraw SHAは過去のmodel metadataを含むlegacy source全体を固定するが、
そのmetadataを新しいgeneration targetへ流用しない。7 IDのどれかにcurrent adapter
profileが存在しない、またはprofile IDが一致しない場合はplanを作らず失敗する。

## artifact chain

```text
raw v3 manifest
  -> baseline-plan.json
  -> 7 × generation ledger
  -> 7 × QC v2 snapshot
  -> baseline assemble bundle
     - candidate-set.json
     - manifest-v4.json
     - baseline-reference.json
     - baseline-provenance.json
     - source-runs/**
     - audio/takes/**
     - reference/**
     - baseline-bundle-inventory.json
  -> baseline-curation-v1
  -> baseline finalize
     - release manifest v4
     - immutable decision
     - audit/provenance/inventory
```

各JSONはexact key集合を検証したcanonical UTF-8 JSONで、artifact本体に尾改行を
付けない。`.sha256` markerは対象raw bytesのlowercase SHA-256を固定する。
bundle inventory markerだけは`<64 hex>\n`とする。

## bundle inventory

`baseline-bundle-inventory.json`のexact shape:

```json
{
  "format_version": 1,
  "files": [
    {
      "path": "baseline-plan.json",
      "sha256": "<64 lowercase hex>"
    }
  ]
}
```

- root keyは`format_version`と`files`だけ。
- item keyは`path`と`sha256`だけ。
- `files`はordinal path昇順。
- pathは非空のcanonical POSIX relative path。
- absolute、`.`、`..`、backslash、重複、casefold衝突を拒否する。
- inventory JSONとmarker自身だけを除外し、bundle内の全fileをexactに列挙する。
- missing、extra、raw SHA不一致をbrowserとpipelineの両方で拒否する。

inventoryはbundleの物理byte閉包である。QC v2、ledger、sidecarの意味契約は
Python finalizeが検証し、browserに同じvalidatorを複製しない。

## referenceとcandidate

旧公開Opusは`reference/<model>/<scenario>/<line>/<variant>.opus`へcopyし、
選択不能にする。`baseline-reference.json`はcandidate set SHAを固定し、
各plan groupについて旧path/SHA、new candidate SHAまたはnull、
`identical|different|no_candidate`を記録する。

新candidateだけがrubricとdecisionの対象である。byte identicalでも自動選択せず、
旧公開されていた事実を人間選択の証拠にしない。

## rubricとdecision

- `content_correct`: 台詞内容に加え、厳密な日本語の音調・アクセントまで含む。
- `intent_match`: scene、emotion、intensity、deliveryへの一致度。
- `character_naturalness`: 役としての自然さ。
- `adoptable`: 感情、自然さ、音質などを含む総合的な利用可能性。

`content_correct`と`adoptable`は独立軸である。例えば感情や自然さが十分でも、
厳密な日本語音調が不正なら
`content_correct=false && adoptable=true`になり得る。

N=3 pilotの`selected`はgroup内の相対winnerだったが、公開baselineの
`selected`はproduction採用決定である。したがって
`content_correct=true && adoptable=true`を必須とする。条件を満たさない
candidateを相対的に最良と判断しても、公開baseline decisionは`skipped`にする。

`baseline-curation-v1`はcandidate set SHAとbaseline reference SHAの両方を
固定し、全candidate groupにselectedまたはskippedを要求する。
candidate-zero groupはdecisionへ偽のcandidateを追加せず、pipeline auditで数える。

## finalize条件

- raw plan、7 source run、aggregate candidate/referenceのexact coverageが一致する。
- 全terminal attemptのledger、QC、sidecar、WAV、Opus joinとSHAが一致する。
- inventoryはsemantic validationの前後で同一である。
- selectedは全件、content correctかつadoptableである。
- `381 = candidate_zero + selected + skipped`
- `uncurated = 0`

どれか1件でも満たさない場合はrelease directoryを作らず失敗する。
