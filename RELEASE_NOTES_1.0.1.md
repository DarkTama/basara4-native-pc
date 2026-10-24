# Basara 4 PC 1.0.1

**English** | [Bahasa Indonesia](#bahasa-indonesia)

The first update after 1.0.0. It fixes music that stopped, black / red screens and wrong pictures in event cutscenes, voices and doubled
sound, and a crash in the stage select. Full list with the reason for each fix:
[CHANGELOG.md](https://github.com/razzconnect/basara4-native-pc/blob/main/CHANGELOG.md).

## Highlights

- **Battle music now loops** like the original (a track used to play once and fall silent). Tracks that start in an unusual order no longer die.
- **Event cutscenes no longer go black, red or show a doubled half-size picture.** The cause was a GPU device loss on NVIDIA: a depth image was used as a colour texture.
- **Dialogue and cutscene voices, doubled sound, harsh clipping and wrong stereo pairs** are fixed in the software Ngs2 audio engine.
- **The stage select no longer crashes** (issue #1).
- **Crash dumps** are written to `user\crash\` and a GPU that stops answering is reported with the draw that caused it.
  A dump contains the memory of the game: only give it to people you trust.

## Install

1. Download `basara4-windows.zip` below and extract it.
2. Put your own legally dumped game (the folder with `eboot.bin`, tested: CUSA01159) in `game\`, or use **Browse** in the launcher.
3. Open `Sengoku Basara 4.exe` and press **PLAY**. The first start prepares the game image (about a minute).

No game files are included.

## Tested on, and known limits

- Tested on **one machine only: Windows with an NVIDIA RTX 4070**. AMD and Intel graphics are untested; the GPU fixes follow the Vulkan rules and should not hurt them, but this is not proven.
- The music loop can have a short gap at the seam (10 to 80 ms measured) on some tracks.
- Frame rates above 60 are still experimental.
- If a music change misbehaves, set the environment variable `SPARTAN_NGS2_NO_LOOP_BLOCKS=1` to get the old behaviour and report it with `user\audio_debug.log`
  (tick **Audio debug log** in the launcher).

SHA-256 of `basara4-windows.zip`: `<isi dengan hasil Get-FileHash>`

---

## Bahasa Indonesia

Pembaruan pertama setelah 1.0.0. Memperbaiki musik yang berhenti, black / red screen dan gambar yang salah di cutscene event, suara dan suara dobel,
serta crash di pemilihan stage. Daftar lengkap beserta alasannya:
[CHANGELOG.id.md](https://github.com/razzconnect/basara4-native-pc/blob/main/CHANGELOG.id.md).

### Sorotan

- **Musik battle sekarang loop** seperti aslinya (dulu satu trek hanya diputar sekali lalu hening). Trek yang dimulai dengan urutan tidak biasa tidak lagi mati.
- **Cutscene event tidak lagi black screen, merah, atau menampilkan gambar dobel separuh ukuran.** Penyebabnya GPU device loss di NVIDIA: depth image dipakai sebagai tekstur warna.
- **Suara dialog dan cutscene, suara dobel, clipping kasar, dan pasangan stereo yang salah** diperbaiki di mesin audio software Ngs2.
- **Pemilihan stage tidak lagi crash** (issue #1).
- **Crash dump** ditulis ke `user\crash\`, dan GPU yang berhenti merespons dilaporkan beserta draw penyebabnya.
  Dump berisi memori game: berikan hanya kepada orang yang Anda percaya.

### Pasang

1. Unduh `basara4-windows.zip` di bawah, lalu ekstrak.
2. Taruh dump game legal milik sendiri (folder berisi `eboot.bin`, teruji: CUSA01159) di `game\`, atau pakai **Browse** di launcher.
3. Buka `Sengoku Basara 4.exe` lalu tekan **PLAY**. Start pertama menyiapkan image game (sekitar satu menit).

File game tidak disertakan.

### Diuji di mana, dan batasannya

- Diuji di **satu mesin saja: Windows dengan NVIDIA RTX 4070**. Grafis AMD dan Intel belum diuji; perbaikan GPU mengikuti aturan Vulkan dan tidak seharusnya merugikannya, tetapi belum terbukti.
- Loop musik bisa punya jeda singkat di sambungan (terukur 10 sampai 80 ms) pada sebagian trek.
- Frame rate di atas 60 masih eksperimental.
- Bila perubahan musik bermasalah, set variabel lingkungan `SPARTAN_NGS2_NO_LOOP_BLOCKS=1` untuk perilaku lama dan laporkan bersama `user\audio_debug.log`
  (centang **Audio debug log** di launcher).
