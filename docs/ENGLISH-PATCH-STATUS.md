# English Translation Patch Status

Current translation status and known gaps when importing the PS3 community translation patch (v0.85 + Matabe fix) via `launcher/basara4_repatch.py`.

## What Is Translated
- Character select screen and character names
- Weapon names and skill names
- Help and description text
- Drama Route text
- Button prompts

## What Remains in Japanese

### In-Game UI Elements
- **Battle-prep menu headers and tabs**: 合戦準備, 武器装備, 取得技, 衣装変更, 武将成長, 装備/強化/合成/解体
- **Stat labels**: 体力, 攻撃, 防御, 気合, バサラゲージ, 攻撃力, 防御力
- **Item and currency labels**: 所持金, 強化印籠, 砥石, 所持数, 玉鋼, おみくじ小づち
- **Route selection names**: 戦国創世・極 ルート, 選択可能ルート
- **Navigation and helper prompts**: R2 武将切替, L2 銘一覧, 並び替え

*Note:* Many of these remaining UI elements are suspected to be textures in skipped formats, but this has not yet been verified per screen (some may be untranslated GMD string entries).

### Untranslated in Original PS3 Patch
- **Story dialogue**: Never translated in the PS3 fan patch.
- **Battle banter / in-stage dialogue**: Never translated in the PS3 fan patch.

## Import Statistics (Launcher v1.0.6)
Verified metrics from a full import run recorded in `mods/english_patch/manifest.json`:

- **Archives written**: 2,177
- **GMD strings replaced**: 9,135
- **Textures translated**: 20,912
- **Textures kept (unmodified PS4 originals)**: 33,830
- **Textures skipped**:
  - `fmt_unsupported`: 11,065
  - `tiling_unsupported`: 58
  - `unmatched_ps4`: 6

## Suspected Cause
The repatcher (`launcher/basara4_repatch.py`) currently only converts TEX format 42 (BC3) textures with a single mip level. Other texture formats and multi-mip textures are skipped and retain their original PS4 Japanese textures (11,065 textures skipped due to `fmt_unsupported`, plus 58 for unsupported tiling).

## Next Steps
- Identify the formats of the 11,065 skipped textures.
- Add decoders and encoders for the missing texture formats and multi-mip layouts.
- Check which remaining Japanese UI labels originate from GMD string tables versus TEX textures.
