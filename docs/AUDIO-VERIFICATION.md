# Audio Basara 4 PC: perbaikan dan cara memeriksanya

Dokumen ini menjelaskan **bagaimana suara dihasilkan** di port ini, **apa saja yang diperbaiki** di mesin audio (Ngs2), dan **langkah demi langkah cara memeriksa**
bahwa tiap perbaikan benar-benar bekerja: alat yang dipakai, apa yang dilihat di log dan di berkas WAV, angka yang harus keluar, dan kapan hasilnya dianggap gagal.

Semua angka contoh di sini nyata, diambil dari sesi uji tanggal 25 Oktober 2026 (Windows, NVIDIA RTX 4070, build `3e63e72`), kecuali jika disebut lain.
Bagian yang **belum bisa diukur secara otomatis** ditandai jujur sebagai "manual" atau "tidak terverifikasi independen". Itu bukan kelalaian dokumen; memang begitu keadaannya.

## Isi

0. [Cara memakai dokumen ini](#0-cara-memakai-dokumen-ini)
1. [Gambaran: dari game sampai speaker](#1-gambaran-dari-game-sampai-speaker)
2. [Alat dan berkas bukti](#2-alat-dan-berkas-bukti)
3. [Metode umum menyelidiki masalah audio](#3-metode-umum-menyelidiki-masalah-audio)
4. [Perbaikan satu per satu dan cara memeriksanya](#4-perbaikan-satu-per-satu-dan-cara-memeriksanya)
5. [Prosedur uji lengkap (satu sesi, semua bukti)](#5-prosedur-uji-lengkap-satu-sesi-semua-bukti)
6. [Uji pembanding: mengembalikan perilaku lama](#6-uji-pembanding-mengembalikan-perilaku-lama)
7. [Keterbatasan metode](#7-keterbatasan-metode)
8. [Lampiran](#8-lampiran)

---

## 0. Cara memakai dokumen ini

Ada tiga tingkat pemeriksaan. Pilih sesuai waktu dan ruang disk Anda.

| Tingkat | Butuh | Waktu | Hasil |
|---|---|---|---|
| **A. Cepat** | hanya **Audio debug log** (kecil, maks 64 MB) | sesi main apa saja + 2 menit analisis | menemukan voice yang macet, kegagalan decoder, event penting, loop berjalan atau tidak (dari baris `block_end`) |
| **B. Lengkap** | log **dan WAV capture** (sampai 3 GB, lihat [peringatan](#23-capture-wav)) | sesi 20 menit + 5 menit analisis | semua tingkat A, ditambah bukti terukur: loop identik sampel demi sampel, hening di mix, clipping |
| **C. Regresi per perbaikan** | bergantung perbaikan | per [bagian 4](#4-perbaikan-satu-per-satu-dan-cara-memeriksanya) | memeriksa satu perbaikan secara khusus, termasuk uji pembanding ([bagian 6](#6-uji-pembanding-mengembalikan-perilaku-lama)) |

Alur yang disarankan sebelum rilis: lakukan **prosedur di bagian 5** (tingkat B) satu kali. Jika semua kriteria di tabel akhir bagian 5 lolos, audio dianggap lulus.

**Tempat menjalankan perintah.** Alat bantu ada di folder `tools\` pada **repositori** (paket pemain tidak memuatnya). Jalankan perintah dari folder repositori dan beri
path lengkap folder `user\` game, misalnya:

```powershell
cd <folder repositori>
$U = "C:\Games\basara4-windows\user"        # ganti dengan folder user\ game Anda
python tools\audio_session_report.py $U
```

Di seluruh dokumen ini `user` dalam contoh perintah berarti folder itu (`$U`).

Prinsip yang dipakai di seluruh dokumen:

1. **Ukur, jangan hanya mendengar.** Telinga berguna untuk melaporkan gejala, tetapi buktinya log dan WAV.
2. **Satu sesi, semua bukti.** Sesi bermain 15 sampai 20 menit mahal. Nyalakan semua perekam sebelum mulai, simpan semuanya sesudahnya.
3. **Jam dinding.** Gejala dilaporkan dengan jam ("01:15 musik mati"); log memakai jam sendiri. Selalu cocokkan keduanya ([2.5](#25-mencocokkan-jam-dinding-dengan-jam-log)).
4. **Ada kontrol.** Bila mungkin, kembalikan perilaku lama dengan saklar ([bagian 6](#6-uji-pembanding-mengembalikan-perilaku-lama)) dan pastikan gejalanya muncul lagi; itu membuktikan perbaikannya yang bekerja, bukan kebetulan.

---

## 1. Gambaran: dari game sampai speaker

### 1.1 Rantai suara

```
 game (x86-64 asli)
    │  memanggil API Sony: sceNgs2* (graf suara) dan sceAudioOut* (keluaran)
    ▼
 src/runtime_offline.c   mesin Ngs2 perangkat lunak
    │  - rack, voice, patch/port, event, parameter
    │  - mendekode ATRAC9 (LibAtrac9) dan PCM
    │  - mencampur voice → submix → mastering
    ▼
 src/runtime_audio.c     AudioOut: port, buffer 256 frame, pacing, limiter, downmix
    ▼
 WASAPI (device_channels=2, 48000 Hz)  →  speaker
```

Bukti rantai ini ada di baris log awal sesi:

```
[       0 ms] log.start format=1 audio_mode=default
[     101 ms] ngs2.system grain=256 rate=48000
[     123 ms] audioout.backend driver=wasapi device_channels=2 device_rate=48000 device_frames=1024
[     123 ms] audioout.open port=0 type=0(main) channels=8 format=float frames=256 stream=1 downmix=1
[     123 ms] audioout.open port=8 type=1(bgm)  channels=8 format=float frames=256 stream=1 downmix=1
```

Satu **grain** = 256 frame pada 48000 Hz = 5,333 ms. Game menyerahkan satu buffer per grain per port. Karena itu **187,5 buffer per detik** per port adalah angka acuan
(lihat `audioout.stats` di [4.1](#41-pacing-dan-starvation-buffer-66131a4)).

### 1.2 Rack, voice, dan port

Game membuat beberapa **rack** Ngs2. Di Basara 4 yang tampak di log (`rack=0x1000/23` artinya jenis rack `0x1000`, nomor seri `23`):

| Rack | Jenis | Peran yang terlihat |
|---|---|---|
| `0x1000/22` | sampler | efek suara dan dialog, voice mono (ATRAC9 mono, config `0xf00570fe`) |
| `0x1000/23` | sampler | musik streaming dan voice event 6 kanal (config `0xf00b74fe` untuk BGM stereo, `0xf01876fe` untuk 6 kanal) |
| `0x1000/24` | sampler | cincin PCM untuk cutscene |
| `0x2000`, `0x2001` | submix | submix kering (`0x2000`) dan submix basah/reverb (`0x2001`) |
| `0x3000` | mastering | keluaran akhir yang diserahkan ke AudioOut |

Setiap voice punya **patch** ke rack berikutnya, tertulis di akhir baris `ngs2.start`:

```
p0->0x2000/0@1.00(m0) p1->0x2001/0@1.00(m1)
```

Artinya: port 0 voice ini ke submix kering `0x2000` voice 0 dengan level 1,00, port 1 ke submix basah `0x2001`.

Voice **mayor** (`major=1`) adalah voice panjang: sedang streaming, atau jenis bukan PCM kecil, atau data ATRAC9 minimal 24.576 byte (PCM 131.072 byte).
Hanya voice mayor yang dicatat rinci dan direkam ke WAV.

### 1.3 Format suara

| Kode jenis | Arti | Dipakai untuk |
|---|---|---|
| `0x40` | ATRAC9 | BGM, dialog, efek, suara event |
| `0x10`, `0x12` | PCM 16-bit | cincin cutscene dan sebagian efek |

File `.sspr` berisi RIFF/WAVE ATRAC9. Chunk **`smpl`** di dalamnya menyimpan **titik loop** (`loop=203802..4555805` di log `ngs2.parse_file`).
Itulah dasar perbaikan musik loop ([4.9](#49-musik-loop-a923b5d)).

Satu **superframe** ATRAC9 adalah unit terkecil yang bisa didekode. Untuk config BGM stereo `0xf00b74fe` ukurannya **384 byte** (4 frame × 96 byte; diturunkan dari
bit-bit config dan cocok dengan semua `offset`/`size` di baris `calc_block`, yang selalu kelipatan 384). Untuk voice mono `0xf00570fe`: 192 byte.

### 1.4 Streaming: blok, chunk, dan flag

Musik tidak diserahkan sekaligus. Game membaca file ke **ring buffer** miliknya lalu mengirim **chunk** ke voice (`ngs2.blocks`):

```
ngs2.blocks rack=0x1000/24 voice=0 append=0 playing=0 played_ms=0 queued=1 data=000000101aee0450 flags=0x1 count=1
            b0{off=0 size=98304 rep=0 skip=0 samples=24576 user=0}
```

| Medan | Arti |
|---|---|
| `append` | 1 = ditambahkan ke antrean voice yang sedang memutar; 0 = mengganti isi voice |
| `flags` bit 0 | masih ada chunk lagi setelah ini (streaming aktif) |
| `flags` bit 1 | chunk ini **melanjutkan blok yang sama** (bukan awal blok baru) |
| `off`, `size` | posisi dan panjang byte di buffer/ring |
| `rep` | pengulangan blok (`4294967295` = tak berhingga) |
| `skip` | sampel di awal yang dibuang (lead-in decoder) |
| `samples` | jumlah sampel blok |

Chunk terakhir sebuah stream punya `flags` bit 0 **mati** = "tidak ada lagi" (`stream_final`).

### 1.5 `calc_block`: game bertanya "byte mana?"

Sebelum memutar musik dari posisi tertentu, game memanggil `sceNgs2CalcWaveformBlocks` untuk bertanya byte mana yang harus dibaca dari file agar dapat sampel tertentu:

```
ngs2.calc_block type=0x40 channels=2 rate=48000 config=0xf00b74fe frame_offset=168 frame_margin=256
                position=125599 samples=3748752 -> offset=46848 size=1453056 skip=927 samples=3748752 rc=0
                extra_args=1099511627776,2304000
```

- `position` = sampel awal yang diminta game (di sini 125.599).
- `samples` (pertama) = jumlah sampel yang diminta game; **panjang putaran pertama = samples − position** = 3.748.752 − 125.599 = **3.623.153 sampel = 75,48 detik**.
- Jawaban kita: `offset` (kelipatan superframe), `size` (dalam konvensi Sony: **offset akhir**, bukan panjang), `skip`.
- `extra_args` (nilai kedua) = jumlah sampel **satu putaran loop** (2.304.000 = 48,00 detik).

### 1.6 Event pada voice

| Event | Nama di kode | Arti |
|---|---|---|
| 0 | `RESET` | Play: mulai dari awal |
| 1 | `PAUSE` | "event 1": dikirim dua kali ke voice yang selesai atau terpotong |
| 2 | `STOP` | berhenti |
| 3 | `KILL` | berhenti paksa |
| 4, 5 | `RESUME` | lanjutkan |

Game juga memantau voice lewat `GetStateFlags`; bila voice terlihat masih "playing", game bisa menunggu selamanya ([4.8](#48-event-1-menghentikan-voice-697bb08)).

---

## 2. Alat dan berkas bukti

### 2.1 Saklar perekaman

| Cara | Efek |
|---|---|
| Launcher: centang **Audio debug log** | menulis `user\audio_debug.log` (variabel `SPARTAN_AUDIO_DEBUG`), log sebelumnya disimpan sebagai `audio_debug.previous.log` |
| Launcher: centang **+ WAV capture (up to 3 GB)** | juga menulis WAV ke `user\audio_wav\` (`SPARTAN_AUDIO_WAV_DIR`). Ada peringatan, berlaku untuk **satu** kali start |
| `SPARTAN_AUDIO_WAV_CH=N` | jumlah kanal pertama yang direkam di `master_*` (bawaan 2, maksimum 8) |
| `SPARTAN_NGS2_TRACE=1` | tiap sekitar 4 detik mencetak voice yang sedang main, rutenya, dan puncak keluarannya |
| `SPARTAN_AUDIO_STATS=1`, `SPARTAN_AUDIO_DUMP=<berkas>` | statistik pacing AudioOut; dump PCM mentah per port (untuk kasus khusus) |
| `SPARTAN_AUDIO=none` | tanpa perangkat audio (uji tanpa speaker) |

Menjalankan tanpa launcher (contoh, di `cmd`):

```
set SPARTAN_AUDIO_DEBUG=C:\Temp\b4\audio_debug.log
set SPARTAN_AUDIO_WAV_DIR=C:\Temp\b4\wav        (folder harus sudah ada)
```

### 2.2 `audio_debug.log`

Satu baris per kejadian: `[  jam ms] jenis kunci=nilai ...`. **Jam log** adalah milidetik sejak `log.start` (sekitar waktu proses dimulai).
Ukuran dibatasi **64 MB**. Banyak jenis baris punya **batas jumlah** (misalnya `calc_block` 60 baris, `play_ignored` 400, `ngs2.event` 1500, `resync` 40):

> **Penting:** jika sebuah baris tidak muncul, bisa jadi memang tidak terjadi, bisa juga batasnya sudah terlampaui. Sebelum menyimpulkan "tidak terjadi",
> hitung jumlah baris jenis itu. Karena batas inilah beberapa baris `ngs2.blocks` hilang saat menyelidiki musik yang macet.

Jenis baris yang dipakai di dokumen ini:

| Jenis | Isi |
|---|---|
| `audioout.backend/open/close/volume` | perangkat, port yang dibuka, volume |
| `audioout.stats` | tiap sekitar 5 detik per port: `buffers` (≈940 per jendela), `silent_calls`, `loud`, `peak` |
| `audioout.first_sound` | puncak pertama yang terdengar di sebuah port |
| `ngs2.system`, `ngs2.rack` | grain, laju, rack yang dibuat |
| `ngs2.start` | sebuah voice mulai bermain: jenis, kanal, config, ukuran, `streaming`, `major`, patch |
| `ngs2.blocks` | chunk diterima (lihat [1.4](#14-streaming-blok-chunk-dan-flag)) |
| `ngs2.calc_block`, `ngs2.parse_file` | pertanyaan posisi byte dan struktur file (blok dan loop) |
| `ngs2.event` | setiap event pada voice panjang, dengan keadaan *sebelum* event |
| `ngs2.state` | panggilan state pertama untuk voice |
| `ngs2.starved`, `ngs2.refilled` | voice streaming kehabisan data / data datang lagi, dengan jumlah chunk antre (`queued`) |
| `ngs2.block_end` | satu blok selesai diputar (akhir putaran): `played_ms`, `dropped_chunks`, `queued`, `next_samples`, `final` |
| `ngs2.end` | voice selesai (`reason=finished` atau `stream_complete`) |
| `ngs2.play_ignored` | Play diabaikan karena voice sedang memutar stream dengan data antre |
| `ngs2.stream_begin_late` | chunk pertama sebuah stream baru datang setelah Play atau pada voice bekas, dan decoder dimulai bersih |
| `ngs2.resync` | decoder gagal terus dan disinkronkan ulang di awal blok berikutnya |
| `ngs2.at9_waiting_for_data`, `ngs2.at9_decode_failed`, `ngs2.at9_init_failed`, `ngs2.at9_no_config`, `ngs2.at9_no_audio` | masalah dekode ATRAC9 |
| `ngs2.voice` | ringkasan voice yang sedang main (tiap jendela): `routed`, `peak`, `played_ms`, `state_calls`, `streaming`, `queued`, `flag_calls` |
| `ngs2.summary` | tiap jendela: jumlah voice yang main, puncak master, jumlah start (`major`, `minor`) |
| `ngs2.callsite` | siapa (alamat kembali di kode game) yang memanggil API state, dihitung per lokasi |
| `wav.open`, `wav.budget_reached` | berkas WAV dibuka; batas 3 GB tercapai |

`played_ms` = sampel yang sudah dikonsumsi ÷ laju sampel × 1000.

### 2.3 Capture WAV

> **Peringatan ukuran.** Sekitar 70 sampai 130 MB per menit. Sesi 17 menit di mesin ini menghasilkan **2,27 GB** (319 berkas). Batas keras 3 GiB per sesi (`wav.budget_reached`).
> Folder dikosongkan di awal sesi berikutnya yang memakai WAV; jika WAV dimatikan, file lama **tidak** dihapus otomatis (launcher bertanya saat dibuka).
> Pastikan ruang kosong minimal 4 GB sebelum menyalakannya.

| Berkas | Isi |
|---|---|
| `master_b<N>.wav` | apa yang diserahkan mesin Ngs2 ke AudioOut untuk buffer render `N` (tiga berkas `b0 b1 b2` di sesi ini, masing-masing 1038 detik, 380 MB) |
| `voice_r<seri>_v<indeks>_<ms>.wav` | satu berkas per voice **mayor**, **setelah dekode dan pitch**, sebelum dicampur. `<ms>` adalah jam log saat berkas dibuka (selisih ±1 ms dengan baris `ngs2.start`) |

Format semua WAV: **float32 IEEE**, header 44 byte, 48000 Hz, 2 kanal (kecuali voice sumber lain). Dibaca dengan `numpy.memmap(path, dtype="<f4", offset=44)`.

### 2.4 Alat bantu (folder `tools/`)

| Alat | Fungsi | Perintah |
|---|---|---|
| `audio_log_summary.py` | ringkasan log: port yang berbunyi, jenis waveform, suara panjang, semua jenis kegagalan | `python tools/audio_log_summary.py user/audio_debug.log` |
| `audio_session_report.py` | **mencari masalah sendiri** di seluruh sesi: `SILENT-VOICE`, `STUCK-STARVED`, `DECODE`, `RESTART`, `TWIN`, `MIX-GAP`, `CLIP`, `FAULT` | `python tools/audio_session_report.py user` |
| `audio_loop_check.py` | **memeriksa loop musik** dari WAV: identik sampel demi sampel, geseran, hening di sambungan | `python tools/audio_loop_check.py user` |
| `audio_master_check.py` | puncak dan clipping dari `master_b*.wav` | `python tools/audio_master_check.py user` |
| `debug_watch.py` | linimasa berjam dinding, tangkapan frame, rekaman layar, dan `user\report.txt` saat game ditutup (alat pengembang, lihat [2.5](#25-mencocokkan-jam-dinding-dengan-jam-log)) | dijalankan oleh `Debug run.bat` |

`audio_session_report.py` menampilkan waktunya sebagai `m:ss` pada **jam log**. Arti temuan:

| Temuan | Arti | Perlu dikhawatirkan? |
|---|---|---|
| `STUCK-STARVED` | voice streaming kehabisan data padahal ada chunk antre, dan tidak pulih | **ya**, selalu periksa |
| `DECODE` | kegagalan atau penantian dekode ATRAC9 | **ya** untuk musik (rack 23); lihat bagian 4 |
| `FAULT` | game crash atau assertion (dari `last_run.log`) | **ya** |
| `SILENT-VOICE` | voice panjang menghasilkan nol, atau hening lama di tengah | periksa: pada voice SFX 2 detik hening di dalam suara 20+ detik umumnya biasa; pada musik tidak |
| `MIX-GAP` | mix hening berdetik-detik padahal ada voice panjang berbunyi | periksa jika voice itu penting |
| `CLIP` | mix mencapai skala penuh | periksa ([4.3](#43-limiter-lunak-55ceee4)) |
| `RESTART` | data yang sama dimulai lagi pada voice yang sama dalam waktu singkat | **petunjuk saja**: SFX berulang itu wajar; yang mencurigakan voice dialog ulang kurang dari sekitar 300 ms |
| `TWIN` | dua voice mulai pada data yang sama dalam 150 ms | **biasanya wajar**: game memang membuat pasangan stereo dari dua voice mono |

### 2.5 Mencocokkan jam dinding dengan jam log

Pemain melaporkan jam dinding ("01:15 musik mati"); log memakai ms sejak awal. Dua cara menghubungkannya:

1. **`timeline.log`** (dari `debug_watch.py`) menyalin baris game, GPU, dan audio dengan jam dinding di depannya:
   `01:04:36 [aud] [   58506 ms] ngs2.parse_file ...`. Jam dinding − ms = saat log dimulai (di contoh ini 01:03:37,5, sama dengan waktu proses dimulai 01:03:37).
2. Tanpa `debug_watch`: ambil waktu mulai proses (`Get-Process basara4-core | Select StartTime`) lalu tambahkan jam log.

Rumus: `jam dinding = waktu mulai + ms`. Contoh: musik mati di 01:15:20 dengan proses mulai 01:03:37 → ms = 703.000 (11:43 menit di jam log).

---

## 3. Metode umum menyelidiki masalah audio

Urutan yang dipakai sepanjang pekerjaan ini. Ikuti urutan yang sama untuk masalah baru.

1. **Catat gejala dengan jam dinding.** "Musik mati sekitar 01:15." Jangan tunggu jelas; perkiraan menit sudah cukup.
2. **Kumpulkan sesi lengkap** (tingkat B). Jangan menutup game setelah gejala; lanjutkan sedikit lalu tutup normal supaya laporan dibuat.
3. **Jalankan `audio_session_report.py`.** Lihat temuan di sekitar waktu gejala (ubah jam dinding ke jam log).
4. **Baca log di sekitar waktu itu.** Cari `ngs2.starved`, `at9_*`, `block_end`, `end`, `event`. Hitung dulu apakah batas jumlah baris sudah tercapai.
5. **Periksa WAV voice yang bersangkutan.** Apakah hening, apakah isinya benar (`audio_loop_check.py` untuk musik loop), apakah ada di `master_*`.
6. **Rumuskan dugaan dengan angka.** Contoh nyata: "trek mulai 580,1 detik; putaran pertama 81,9 detik; masalah mulai 663,4 detik = 83,3 detik setelah mulai ≈ tepat batas loop" → dugaan: transisi loop gagal.
7. **Buktikan dugaan sebelum mengubah kode** bila bisa: baca kode game (alamat tamu = `0x800000000` + offset image; lihat [8.4](#84-membaca-kode-game)), cocokkan dengan protokol di log.
8. **Perbaiki sekecil mungkin**, beri nama saklar kembali ke perilaku lama bila masuk akal, tambahkan baris log yang membuktikan jalur baru dipakai (misalnya `ngs2.stream_begin_late`).
9. **Ulangi sesi dan ukur.** Kriteria lulus ditulis *sebelum* menjalankan.
10. **Jangan menyimpulkan dari satu pengamatan.** Perbaikan pertama untuk musik mati ternyata hanya menutup satu dari tiga urutan kejadian; dua sesi berturut-turut menemukan pola baru.

---

## 4. Perbaikan satu per satu dan cara memeriksanya

Tiap bagian: **Gejala** → **Penyebab** → **Perubahan** → **Cara memeriksa** (langkah dan kriteria) → **Tingkat bukti**.
Tingkat bukti: **terukur** (angka dari log/WAV), **terukur sebagian**, atau **manual/tidak terverifikasi independen**.

### 4.1 Pacing dan starvation buffer (`66131a4`)

- **Gejala.** Audio cutscene tersendat atau hening; suara tidak melanjutkan setelah pergantian stream.
- **Penyebab (dari perubahan kode).** Thread audio yang sama mengirim beberapa port berurutan dalam satu grain sehingga tidur dua kali; voice yang diganti isinya tidak
  di-unpause; matriks level/patch Ngs2 tidak diterapkan; nilai level port `> 0` dianggap "tersalurkan" padahal ada level negatif.
- **Perubahan.** Tidur pacing hanya sekali per grain per thread; blok yang menggantikan isi voice melepas pause; matriks level per voice dan indeks output mastering diterapkan;
  rute dinilai dengan `|level| > 1e-6`.
- **Cara memeriksa.**
  1. Nyalakan log. Mainkan sampai ada cutscene.
  2. Ambil semua baris `audioout.stats`: `Select-String user\audio_debug.log -Pattern 'audioout\.stats'`.
  3. **Kriteria:** untuk tiap port, `buffers` per jendela sekitar 5 detik berkisar **900 sampai 990** (acuan 187,5 per detik ≈ 940 per jendela, toleransi ±5%); `silent_calls=0`.
     Di sesi acuan: 615 baris, `buffers` terendah 924, tertinggi 963, rata-rata 945, `silent_calls` selalu 0.
  4. Jalankan `audio_session_report.py`: tidak ada `MIX-GAP` pada voice cutscene (rack 24).
- **Tingkat bukti.** Terukur sebagian (pacing terukur; "tersendat" bergantung telinga).

### 4.2 `calc_block`: offset akhir dan perataan superframe (`ab1f30e`, `9bf1bf2`)

- **Gejala.** Musik battle dibaca dari tempat atau dengan panjang yang salah (ukuran seek keliru).
- **Penyebab.** Game (Capcom MT Framework) menjumlahkan sendiri offset awal data (`frame_offset`) ke hasil kita; kita menambahkannya juga (dobel). Dan `size` harus berupa **offset akhir** (konvensi Sony), bukan panjang.
- **Perubahan.** `offset` = kelipatan superframe tanpa `frame_offset` lagi; `size = offset + panjang`; lead-in satu superframe dihitung lewat `skip`.
- **Cara memeriksa.** Lihat baris `ngs2.calc_block` di log:
  1. `offset` dan `size` harus **kelipatan 384** (untuk config `0xf00b74fe`). Contoh benar: `offset=46848` (= 122 × 384), `size=1453056` (= 3784 × 384).
  2. `skip` < ukuran satu superframe dalam sampel (di contoh 927).
  3. Setelah baris itu, voice berbunyi dari musik yang benar. Ukur lewat WAV: panjang putaran pertama = `samples − position` (lihat [1.5](#15-calc_block-game-bertanya-byte-mana)) harus sama dengan waktu `played_ms` pada `block_end` pertama (contoh 79.643 ms ≈ 3.822.881 ÷ 48).
  4. `ngs2.at9_decode_failed` dan `at9_waiting_for_data` tidak ada pada rack 23.
- **Tingkat bukti.** Terukur (format) dan terukur sebagian (kebenaran isi musik).

### 4.3 Limiter lunak (`55ceee4`)

- **Gejala.** Bunyi pecah/kasar saat banyak efek keras menjumlah (clipping digital).
- **Perubahan.** Fungsi `audio_soft_limit`: lurus sampai ±0,7, di atasnya saturasi `0,7 + 0,295·tanh((x − 0,7)/0,295)`. Dipakai pada downmix stereo dan konversi keluaran.
- **Cara memeriksa.**
  1. Pakai WAV capture, lalu jalankan `python tools/audio_master_check.py user`. Alat ini membaca `master_b*.wav` dan mencetak puncak serta jumlah sampel di atas 0,999 (puncak datar = clipping).
  2. **Kriteria:** "samples at or above 0.999" = 0 pada semua berkas, dan `audio_session_report.py` tanpa `CLIP`. Keluar kode 1 bila ada clipping. Sesi acuan: puncak 0,5043 / 0,2226 / 0,0738, nol sampel di atas 0,999.
  3. Sesi acuan tidak memuat adegan sekeras mungkin; untuk uji keras, mainkan musou ramai dan ulangi.
- **Tingkat bukti.** Terukur untuk "tidak ada clipping di sesi ini"; efek pada kualitas bunyi **manual**.

### 4.4 Submix basah dibisukan, flag lanjutan, antrean 64 blok (`1d496a7`)

- **Gejala.** Dialog terdengar bergema/mengalami flanging; stream panjang kehilangan lanjutan chunk.
- **Penyebab.** Voice sampler mengirim sinyal kering penuh ke submix basah `0x2001` (tanpa DSP reverb), sehingga salinan yang sefase dijumlahkan kembali ke master. Selain itu flag bit 1 (lanjutan) tidak dikenali dan antrean hanya 16 blok.
- **Perubahan.** Voice sampler yang menuju `0x2001` dilewati; `append` memakai `(flags & 3) != 0`; `NGS2_MAX_BLOCKS` 16 → 64.
- **Cara memeriksa.**
  1. Antrean: pada log tidak ada `ngs2.blocks_truncated` dan `queued` tidak pernah menyentuh 64 pada rack 23 (`Select-String user\audio_debug.log -Pattern 'blocks_truncated'` kosong).
  2. Flag lanjutan: baris `ngs2.blocks` dengan `flags=0x3` memakai `append=1` pada voice yang sedang main.
  3. Tanpa gema: **manual**. Bandingkan puncak `master_*` saat satu voice dialog tunggal main dengan puncak WAV voice itu; jika dua salinan sefase dijumlahkan, puncaknya mendekati dua kali lipat. Belum ada alat otomatis untuk ini.
  4. Tidak ada saklar untuk mengembalikan perilaku lama, jadi tidak ada uji pembanding.
- **Tingkat bukti.** Terukur sebagian untuk antrean/flag; "tanpa gema" **manual**.

### 4.5 Voice kembar mono dipan kiri dan kanan (`f194524`)

- **Gejala.** Dialog/efek terdengar dobel di tengah (flanging).
- **Penyebab.** MT Framework membuat pseudo-stereo dengan dua voice mono pada data yang sama, dimulai pada milidetik yang sama.
- **Perubahan.** Dua voice mono pada rack sampler dengan data yang sama dalam 50 ms: yang pertama condong kiri (kiri 1,0 / kanan 0,05), yang kedua kanan (0,05 / 1,0).
  Juga penjagaan agar voice streaming yang menunggu data tidak mengeluarkan sisa buffer.
- **Cara memeriksa.**
  1. `audio_session_report.py` melaporkan `TWIN` untuk pasangan itu (jumlah dan waktunya wajar; contoh sesi acuan 30 temuan TWIN, semua pada rack 22).
  2. Panning: **manual**. Bandingkan energi kiri dan kanan di `master_*` pada saat TWIN; yang satu harus jauh lebih besar di tiap sisi. Belum diotomatisasi.
  3. Bahwa panning ini sesuai maksud game belum terbukti independen; `TWIN` sendiri "biasanya wajar".
- **Tingkat bukti.** **Manual / tidak terverifikasi independen.**

### 4.6 Mengganti blok tidak menjalankan voice; Play pada stream berjalan bukan restart (`d234359`, disempurnakan `3e63e72`)

- **Gejala.** Kata dialog terulang; musik/suara mulai sendiri; sampah di awal.
- **Penyebab.** Setiap penggantian blok otomatis memutar, dan setiap Play pada voice yang sedang memutar stream mengulang decoder dari byte 0 di ring yang sudah terisi ulang.
- **Perubahan.**
  - Mengganti blok (`append=0`) hanya menandai voice `fresh`; memutar baru saat Play.
  - Play diabaikan (`ngs2.play_ignored`) hanya jika voice sedang memutar, tidak pause, tidak `fresh`, **dan ada chunk di antrean** (syarat terakhir ditambahkan di `3e63e72`).
- **Cara memeriksa.**
  1. Pada `ngs2.blocks append=0 playing=0` tidak ada `ngs2.start` di baris yang sama; `ngs2.start` muncul setelah `ngs2.event id=0`.
  2. `Select-String user\audio_debug.log -Pattern 'play_ignored'`: boleh tidak ada (pada sesi acuan **0 baris**, jalur ini tidak terpicu); jika ada, `queued` pada baris itu harus **lebih dari 0**
     (Play dengan antrean kosong harus diterima, bukan diabaikan).
  3. Tidak ada `RESTART` pada voice dialog panjang (mayor) kurang dari sekitar 300 ms.
  4. Uji pembanding: `SPARTAN_NGS2_AUTOPLAY_BLOCKS=1` dan `SPARTAN_NGS2_PLAY_RESTARTS=1` mengembalikan perilaku lama ([bagian 6](#6-uji-pembanding-mengembalikan-perilaku-lama)).
- **Tingkat bukti.** Terukur sebagian (jalur kode terbukti lewat log; "kata tidak terulang" didengar).

### 4.7 Stream selesai pada chunk akhir (`27d03e2`, disempurnakan `a923b5d`)

- **Gejala.** Voice dialog yang selesai tetap "playing", mengunci voice dan game menunggunya.
- **Perubahan.** Stream yang seluruh waveform-nya sudah didekode dan chunk terakhirnya punya flag bit 0 mati diakhiri dengan `ngs2.end reason=stream_complete` (`block_end ... final=1`).
- **Cara memeriksa.**
  1. Untuk setiap `ngs2.block_end ... final=1` ada `ngs2.end ... reason=stream_complete` pada jam yang sama.
  2. `final=0` **tidak boleh** diikuti `stream_complete` (itu loop atau blok berikutnya).
  3. Voice yang sudah `ended` tidak muncul lagi di `ngs2.voice` dengan `played_ms` yang terus naik.
- **Tingkat bukti.** Terukur.

### 4.8 Event 1 menghentikan voice (`697bb08`)

- **Gejala.** Layar hitam saat cutscene event dimulai; game berjalan tetapi tidak maju (sekitar 590 polling flag per detik).
- **Penyebab.** Event 1 (dikirim dua kali ke voice yang selesai atau terpotong) diperlakukan sebagai *pause*; voice tetap `playing=1`, `GetStateFlags` terus menjawab 1, dan game menunggu selamanya.
- **Perubahan.** Pada rack sampler, event 1 mengakhiri voice (`playing=0`); Resume tetap melanjutkan dari posisi yang sama. Baris `ngs2.voice` kini memuat `flag_calls`.
- **Cara memeriksa.**
  1. Cari `ngs2.event id=1` dan baris sesudahnya untuk voice itu.
  2. **Kriteria (berbasis laju):** `flag_calls` adalah hitungan kumulatif per voice, jadi nilai absolutnya tidak berarti (voice musik 6 menit mencapai 101.281). Yang dihitung adalah **selisih antar baris `ngs2.voice`** per detik.
     Polling normal = sekitar sekali per grain, **≈ 190 per detik**; game yang terkunci menunggu voice melakukan **≈ 590 per detik**. Perintah (diuji pada log acuan; hasil terburuk 193 per detik dari 2.092 jendela):
     ```powershell
     $rows = Select-String "$U\audio_debug.log" -Pattern 'ngs2\.voice rack=\S+ voice=\d+ .*played_ms=\d+.*flag_calls=\d+' | ForEach-Object {
       if ($_.Line -match '^\[\s*(\d+) ms\] ngs2\.voice rack=(\S+) voice=(\d+) .*played_ms=(\d+).*flag_calls=(\d+)') {
         [pscustomobject]@{ Ms=[int]$Matches[1]; Key="$($Matches[2])/v$($Matches[3])"; Played=[int]$Matches[4]; Flags=[int]$Matches[5] } } }
     $rows | Group-Object Key | ForEach-Object { $prev = $null; foreach ($r in ($_.Group | Sort-Object Ms)) {
         if ($prev -and $r.Played -gt $prev.Played -and $r.Flags -ge $prev.Flags -and ($r.Ms - $prev.Ms) -ge 1000 -and ($r.Ms - $prev.Ms) -le 10000) {
           [pscustomobject]@{ Key=$_.Name; AtMs=$r.Ms; PerSecond=[math]::Round(($r.Flags - $prev.Flags) * 1000.0 / ($r.Ms - $prev.Ms)) } }
         $prev = $r } } | Sort-Object PerSecond -Descending | Select-Object -First 5
     ```
     Lulus bila laju tertinggi **< 250 per detik**. Tidak ada voice dengan `played_ms` tetap tetapi `playing` tetap 1 di jendela-jendela berurutan.
  3. Layar tidak hitam saat cutscene event (`evp…`, `evm…` pada `timeline.log`). Catatan: layar hitam juga bisa berasal dari GPU (device lost di NVIDIA); periksa `DEVICE LOST` di `last_run.log` untuk membedakan.
  4. Uji pembanding: `SPARTAN_NGS2_EVENT1_PAUSES=1` mengembalikan pause lama.
- **Tingkat bukti.** Terukur sebagian. Hubungan langsung dengan layar hitam terlihat bersamaan di log, tetapi **belum dibuktikan** sebagai satu-satunya sebab (penyebab dominan layar hitam yang kemudian ditemukan ada di GPU).

### 4.9 Musik loop (`a923b5d`)

- **Gejala.** Satu trek musik battle hanya diputar sekali (sekitar 75 sampai 92 detik) lalu hening.
- **Penyebab.** File `.sspr` membawa titik loop di chunk `smpl`. Game meminta `loop_end + 1 − 256` sampel lewat `calc_block`, lalu *feeder*-nya (di kode game) mengirim ulang blok yang berulang tanpa henti.
  Kita hanya mengembalikan satu blok yang berakhir; setelah itu tidak ada yang bisa diulang.
- **Perubahan.**
  - `ngs2_parse` mengembalikan **dua blok** untuk file ber-loop:
    - blok 0: dari awal sampai akhir loop, `samples = loop_end + 1 − 256`;
    - blok 1: badan loop, `rep = 4294967295`, dimulai di batas superframe dengan `skip = loop_start − (superframe pertama × sampel per superframe)`.
  - Decoder berhenti tepat setelah jumlah sampel blok, membuang chunk sisa (yang melebihi blok), mereset decoder, lalu memulai blok berikutnya (`ngs2.block_end`).
  - Stream selesai hanya jika chunk terakhir yang dikirim punya flag bit 0 mati.
- **Cara memeriksa (inti dokumen ini).**
  1. **Log:** `Select-String user\audio_debug.log -Pattern 'parse_file path=.*bgm'`. Baris harus memuat `loop=A..B blocks=2 b0{...} b1{... rep=4294967295 ...}`.
     Hitungan acuan: `b0.samples = B + 1 − 256` dan `b1.samples = B + 1 − A`. Contoh: `loop=503136..3831135` → `b0 = 3830880`, `b1 = 3328000`.
  2. **Pas waktunya:** `ngs2.block_end ... final=0`. Putaran pertama selesai di `played_ms ≈ (samples − position) ÷ 48` dan putaran berikutnya `+ (b1 ÷ 48)`.
     Contoh nyata: `block_end played_ms=79643` lalu `148977`; selisih 69.334 ms = 3.328.000 ÷ 48. **Kriteria:** selisih antar `block_end` sama dengan panjang loop ± 2 ms.
  3. **Tidak ada kegagalan:** `Select-String user\audio_debug.log -Pattern 'ngs2\.resync|bad_chunk|at9_waiting_for_data|at9_decode_failed'` kosong; `audio_session_report.py` tanpa `STUCK-STARVED`/`DECODE` pada rack 23.
  4. **Bukti WAV (kriteria utama):** `python tools/audio_loop_check.py user`. Untuk tiap voice musik yang melewati titik loop, alat menyusun dari log panjang putaran pertama (`b0`) dan panjang loop (`loop`)
     lalu membandingkan WAV dengan dirinya sendiri: audio setelah sambungan harus **sama persis** dengan bagian loop dari putaran pertama.
     | Vonis | Arti | Tindakan |
     |---|---|---|
     | `PASS` | identik 100%, geseran 0, tidak ada hening di sambungan | lulus |
     | `SEAM-GAP` | isi benar (korelasi ≥ 0,99) tetapi mulai terlambat/terlalu awal atau ada hening beberapa ms di sambungan | catat; keterbatasan yang diketahui ([4.10](#410-awal-stream-yang-terlambat-decoder-bersih-dan-sinkron-ulang-c7ba3a0-3e63e72)) |
     | `FAIL` | isi setelah sambungan bukan badan loop, atau ada hening ≥ 0,2 detik | **gagal**: selidiki |
     | `NOT-EXERCISED` | voice berakhir sebelum titik loop | bukan bukti apa-apa; main lebih lama |
  5. **Kriteria lulus loop:** minimal satu voice musik `PASS` dan **nol** `FAIL`. Voice `SEAM-GAP` boleh ada dengan celah ≤ 100 ms.
- **Hasil acuan (sesi 25 Okt, 9 voice musik; hasil dari `audio_loop_check.py`):**
  | Voice | Panjang | Vonis | Catatan |
  |---|---|---|---|
  | `voice_r23_v1_390794.wav` (m012) | 203,4 s | **PASS** | putaran 2 dan 3 identik 100%, geseran 0 |
  | `voice_r23_v2_670095.wav` (menu) | 368,4 s | **PASS** | 3 putaran; hanya 2.000 sampel pertama setelah sambungan berbeda sedikit (decoder dimulai ulang), selisih maksimum 0,08 |
  | `voice_r23_v0_46160.wav`, `voice_r23_v0_219797.wav` | 91,3 s, 98,5 s | **SEAM-GAP** | hening 26 ms dan 48 ms, geseran −53,4 ms, korelasi 1,0000 |
  | lima voice lain | 11 s sampai 82 s | NOT-EXERCISED | berakhir sebelum titik loop (termasuk `bgm_m012` offset 6529027, 75,9 s dari 81,9 s) |
- **Uji pembanding.** `SPARTAN_NGS2_NO_LOOP_BLOCKS=1` mengembalikan parse lama: `parse_file` tidak lagi mencetak `blocks=2`, tidak ada `block_end ... final=0`, dan musik hening setelah putaran pertama.
- **Tingkat bukti.** **Terukur** (identik sampel demi sampel).

### 4.10 Awal stream yang terlambat, decoder bersih, dan sinkron ulang (`c7ba3a0`, `3e63e72`)

- **Gejala.** Musik hening selama puluhan detik atau selamanya pada trek tertentu (`bgm_m012` offset 6529027), terjadi dua kali pada dua sesi berbeda dengan pola berbeda.
- **Penyebab (terbukti di log).**
  - Pola 1: game mengirim Play 23 ms **sebelum** chunk pertama; voice masih `streaming=1` dari trek sebelumnya sehingga chunk dianggap *lanjutan*, pelacakan blok tidak pernah dimulai, dan setelah akhir blok decoder membaca ring yang sudah dipakai ulang oleh game (isinya teks "ver.").
    Antrean tumbuh sampai 45 chunk, `STUCK-STARVED` 337 detik.
  - Pola 2: voice masih memutar sisa stream sebelumnya; Play diabaikan; chunk baru didekode dengan **sisa superframe lama** dan posisi lama, sehingga semua superframe baru bergeser. Satu per satu chunk dibuang.
- **Perubahan.**
  - Chunk yang **memulai blok** datang saat antrean kosong dan bukan kelanjutan loop (`stream_wait` tanpa chunk akhir) = **stream baru**: decoder di-reset bersih (`ngs2.stream_begin_late`).
  - Play dengan antrean kosong **diterima**.
  - 60 kali dekode gagal berturut-turut: chunk dibuang sampai awal blok berikutnya (satu-satunya titik yang pasti rata dengan superframe), decoder di-reset dan mulai di sana (`ngs2.resync`).
- **Cara memeriksa.**
  1. `Select-String user\audio_debug.log -Pattern 'stream_begin_late'`: pada sesi acuan 3 baris (`was_final=0` dan `was_final=1`). Setelah tiap baris, voice itu memutar musik: periksa WAV-nya tidak hening (`audio_session_report.py` tanpa `SILENT-VOICE` pada rack 23).
  2. `Select-String ... -Pattern 'ngs2\.resync'`: **idealnya kosong**. Jika ada, itu bukti bahwa jalan pemulihan dipakai; catat baris dan jamnya, dan periksa musik pada saat itu.
  3. `STUCK-STARVED` tidak muncul (di sesi buruk: *"still starved at the end (450.5 s, 3 blocks queued)"*).
  4. Untuk trek yang memiliki urutan "Play dulu": mainkan stage yang menghasilkan trek `bgm_m012` offset 6529027 (`parse_file ... offset=6529027`) **melewati 83 detik** (batas loop pertama pada 81,93 detik) dan pastikan `block_end final=0` muncul dengan `queued ≥ 1`.
     **Ini belum pernah teruji ulang** setelah perbaikan: pada sesi acuan trek itu hanya dimainkan sekitar 76 detik.
- **Tingkat bukti.** Terukur untuk jalur `stream_begin_late` (menu musik loop sempurna setelahnya); **belum teruji** untuk titik macet yang persis sama.

### 4.11 Perubahan yang dicabut (`9fd4c98` dicabut `2acbd18`)

Sebuah "pengaman" yang lebih luas untuk BGM dan dialog sempat ditambahkan lalu dicabut karena membuang sebagian dialog. Jika dialog kembali hilang di sekitar BGM,
periksa bahwa tidak ada kode semacam itu yang kembali (kode `runtime_offline.c` tidak boleh menolak voice sampler berdasarkan "kemiripan data" atau waktu).

---

## 5. Prosedur uji lengkap (satu sesi, semua bukti)

### 5.1 Persiapan (5 menit)

1. Pastikan ruang kosong di drive game **≥ 4 GB**.
2. Buka launcher. Jika muncul pertanyaan menghapus capture lama, jawab **Yes** (hanya berkas `.wav`).
3. Centang **Audio debug log**, lalu **+ WAV capture** (jawab **Yes** pada peringatan).
4. (Opsional, pengembang) jalankan lewat `Debug run.bat` untuk linimasa jam dinding dan rekaman layar. *Rekaman menangkap seluruh layar utama*; tutup jendela pribadi atau set `DEBUG_NO_VIDEO=1`.
5. Catat jam mulai.

### 5.2 Skenario bermain (sekitar 20 menit)

| Urutan | Tujuan | Lama minimal |
|---|---|---|
| 1. Menu utama | musik menu (`bgm_system`) loop | 3 menit (putaran pertama menu ≈ 118 detik) |
| 2. Masuk stage `m012` | musik battle | bertempur **≥ 3 menit** tanpa berpindah (melewati titik loop 75 sampai 92 detik) |
| 3. Biarkan musik berganti fase | trek berbeda | 2 atau 3 pergantian bila ada |
| 4. Cutscene event (`evp…`, `evm…`) | event 1, dialog, suara 6 kanal | 2 kali sampai selesai |
| 5. Dialog panel | dialog panjang | beberapa baris |
| 6. Keluar lewat menu | akhir bersih | |

Jika ada yang aneh, catat jam dinding (atau tekan **F9** bila memakai `Debug run.bat`), lanjut sedikit, lalu **tutup normal**.

### 5.3 Setelah sesi

1. Tutup game dengan normal (laporan dibuat otomatis bila memakai `Debug run.bat`).
2. **Salin bukti sebelum sesi berikutnya menimpanya:**
   ```powershell
   $U = "C:\Games\basara4-windows\user"      # ganti dengan folder user\ game Anda
   $d = "$U\bukti-$(Get-Date -Format yyyyMMdd-HHmm)"; New-Item -ItemType Directory $d | Out-Null
   Copy-Item "$U\audio_debug.log", "$U\last_run.log", "$U\report.txt", "$U\timeline.log" $d -ErrorAction SilentlyContinue
   ```
   (WAV terlalu besar untuk disalin; analisis di tempat, lalu hapus sesuai kebutuhan.)

### 5.4 Analisis (urut)

```powershell
cd <folder repositori>
$U = "C:\Games\basara4-windows\user"      # ganti dengan folder user\ game Anda
$l = "$U\audio_debug.log"

# 1. temuan otomatis
python tools\audio_session_report.py $U

# 2. loop musik
python tools\audio_loop_check.py $U            # keluar kode 1 jika ada FAIL

# 3. sinyal-sinyal kunci (dua baris pertama boleh ada; sisanya diharapkan KOSONG)
Select-String $l -Pattern 'ngs2\.block_end.*final=0' | Select-Object -First 10          # bukti loop berjalan (harus ada bila musik melewati titik loop)
Select-String $l -Pattern 'stream_begin_late'                                             # jalur baru dipakai (boleh ada)
Select-String $l -Pattern 'ngs2\.resync|bad_chunk'                                        # pemulihan (idealnya kosong)
Select-String $l -Pattern 'at9_decode_failed|at9_waiting_for_data|at9_init_failed|at9_no_'  # kegagalan dekode (kosong)
Select-String $l -Pattern 'blocks_truncated|wav\.budget_reached'                          # batas terlampaui (kosong)
Select-String "$U\last_run.log" -Pattern 'DEVICE LOST|Fault|Assertion|Crash dump' -CaseSensitive   # crash/GPU (kosong)

# 4. pacing AudioOut: buffer per jendela sekitar 5 detik harus 900 sampai 990
Select-String $l -Pattern 'audioout\.stats' | ForEach-Object { if ($_.Line -match 'buffers=(\d+)') { [int]$Matches[1] } } | Measure-Object -Minimum -Maximum -Average
Select-String $l -Pattern 'silent_calls=[1-9]'                                           # kosong

# 5. clipping di mix (butuh WAV capture)
python tools\audio_master_check.py $U
```

### 5.5 Kriteria lulus (tabel akhir)

| # | Pemeriksaan | Lulus bila |
|---|---|---|
| 1 | `audio_session_report.py`: `STUCK-STARVED`, `DECODE`, `FAULT` | **nol** pada rack 23 |
| 2 | `SILENT-VOICE` pada musik (rack 23) | nol |
| 3 | `audio_loop_check.py` | nol `FAIL`; minimal satu `PASS` |
| 4 | `block_end ... final=0` | ada, dengan selisih antar baris = panjang loop ± 2 ms |
| 5 | `ngs2.resync` / `bad_chunk` | nol (jika ada, catat dan periksa musik pada saat itu) |
| 6 | `at9_*` gagal | nol |
| 7 | `audioout.stats` `buffers` | 900 sampai 990 per jendela, `silent_calls=0` |
| 8 | clipping (`CLIP`, `audio_master_check.py`) | nol sampel ≥ 0,999 |
| 9 | laju polling `flag_calls` ([4.8](#48-event-1-menghentikan-voice-697bb08)) | tertinggi < 250 per detik |
| 10 | `DEVICE LOST`, crash dump | nol |
| 11 | mendengar: musik tidak berhenti, dialog tidak berulang, tidak ada gema | ya (manual) |

### 5.6 Mencatat hasil

Salin tabel ini ke catatan Anda (atau isian rilis) dan isi:

```
Tanggal/jam:            Build (commit):            Mesin (GPU/OS):
Lama sesi:              Trek musik yang terdengar:
#1 ... #11   lulus / gagal / tidak diuji      (sertakan angka: jumlah SEAM-GAP, jumlah stream_begin_late, dll.)
Catatan jam dinding gejala (jika ada):
```

---

## 6. Uji pembanding: mengembalikan perilaku lama

Saklar berikut mengembalikan perilaku **sebelum** perbaikan. Tujuannya membuktikan gejala lama muncul lagi sehingga perbaikan memang berperan.
Pasang di jendela `cmd` yang sama sebelum menjalankan `bin\basara4-core.exe` secara manual, atau lewat `Play Sengoku Basara 4.cmd` yang diedit sementara.

| Saklar (`=1`) | Mengembalikan | Gejala yang harus muncul lagi | Bagian |
|---|---|---|---|
| `SPARTAN_NGS2_NO_LOOP_BLOCKS` | parse lama tanpa blok loop | musik battle hening setelah putaran pertama; tidak ada `block_end ... final=0` | [4.9](#49-musik-loop-a923b5d) |
| `SPARTAN_NGS2_EVENT1_PAUSES` | event 1 = pause | voice event tetap `playing`, `flag_calls` membengkak, kemungkinan layar hitam di cutscene event | [4.8](#48-event-1-menghentikan-voice-697bb08) |
| `SPARTAN_NGS2_AUTOPLAY_BLOCKS` | mengganti blok langsung memutar | suara mulai sendiri; `ngs2.start` tanpa `event id=0` | [4.6](#46-mengganti-blok-tidak-menjalankan-voice-play-pada-stream-berjalan-bukan-restart-d234359-disempurnakan-3e63e72) |
| `SPARTAN_NGS2_PLAY_RESTARTS` | Play selalu me-restart | kata dialog terulang; `play_ignored` tidak muncul | sama |

Langkah uji pembanding:

1. Jalankan sesi **dengan** saklar. Simpan bukti.
2. Jalankan sesi **tanpa** saklar (bawaan). Simpan bukti.
3. Bandingkan dengan perintah di [5.4](#54-analisis-urut). Perbedaannya adalah bukti perbaikan.

Hati-hati: **jangan merilis dengan saklar menyala**. Saklar hanya untuk diagnosis. Sesi dengan `NO_LOOP_BLOCKS` memang memberi hasil `NOT-EXERCISED`/hening, itu yang diharapkan.

---

## 7. Keterbatasan metode

- **Hanya satu mesin diuji** (Windows, NVIDIA RTX 4070). Waktu dekode dan pacing di CPU lain bisa berbeda; kriteria angka (900 sampai 990 buffer per jendela) tetap berlaku, tetapi belum dicoba.
- **Jam log mengukur sampel yang dikonsumsi**, bukan waktu dinding. Selisih keduanya kecil, tetapi jangan membandingkan `played_ms` dengan jam dinding hingga ke milidetik.
- **Batas jumlah baris log** bisa menyembunyikan kejadian ([2.2](#22-audio_debuglog)). Hitung dulu.
- **WAV voice diambil sebelum pan dan pencampuran.** Karena itu pan ([4.5](#45-voice-kembar-mono-dipan-kiri-dan-kanan-f194524)) dan basah/kering ([4.4](#44-submix-basah-dibisukan-flag-lanjutan-antrean-64-blok-1d496a7)) hanya bisa dinilai lewat `master_*` dan telinga.
- **`audio_loop_check.py` hanya menilai musik berstream** dengan baris `calc_block`/`parse_file` dalam 3 detik sebelum `ngs2.start`. Trek lain tidak dinilai.
- **`SEAM-GAP` adalah keterbatasan yang diketahui**: bila game menyerahkan chunk blok berikutnya terlambat (antrean kosong saat `block_end`), muncul hening 10 sampai 80 ms dan geseran. Belum diperbaiki.
- **Titik macet `bgm_m012` offset 6529027 belum diuji melewati 83 detik** setelah perbaikan terakhir ([4.10](#410-awal-stream-yang-terlambat-decoder-bersih-dan-sinkron-ulang-c7ba3a0-3e63e72)).
- **Tidak ada pembanding dengan konsol asli.** "Benar" berarti konsisten dengan protokol yang diminta game dan dengan dirinya sendiri (loop identik), bukan dengan rekaman PS4.

---

## 8. Lampiran

### 8.1 Rumus dan konversi

| Besaran | Rumus |
|---|---|
| sampel → milidetik | `ms = sampel ÷ 48` (48000 Hz) |
| satu grain | 256 sampel = 5,333 ms; 187,5 grain per detik |
| panjang putaran pertama | `calc_block: samples − position` (tanpa `calc_block`, menu musik: `b0.samples`) |
| `b0.samples` file ber-loop | `loop_end + 1 − 256` |
| panjang badan loop (`b1.samples`) | `loop_end + 1 − loop_start` |
| saat `block_end` ke-n | `played_ms ≈ putaran pertama + (n − 1) × badan loop`, dalam ms |
| ukuran superframe | `config 0xf00b74fe`: 384 byte; `0xf00570fe`: 192 byte |

Contoh dari trek nyata (semua dari `bgm_m012.sspr` kecuali yang disebut menu):

| Offset file | `loop=A..B` | `b0.samples` | posisi awal | putaran pertama | badan loop |
|---|---|---|---|---|---|
| 3229667 | 203802..4555805 | 4555550 | 120799 | 4.434.751 = 92,39 s | 4.352.004 = 90,67 s |
| 4944435 | 503136..3831135 | 3830880 | 7999 | 3.822.881 = 79,64 s | 3.328.000 = 69,33 s |
| 6529027 | 164608..4003647 | 4003392 | 70975 | 3.932.417 = 81,93 s | 3.839.040 = 79,98 s |
| menu (bgm_system) 188277305 | 525325..5679053 | 5678798 | 0 | 5.678.798 = 118,31 s | 5.153.729 = 107,37 s |

Cek hitungan: `3831135 + 1 − 256 = 3830880` dan `3831135 + 1 − 503136 = 3328000`.

### 8.2 Menghitung ulang loop dengan tangan (tanpa alat)

1. Dari `calc_block`: `b0 = samples − position`, `loop = nilai kedua extra_args`.
2. Buka WAV voice. Bandingkan potongan 10 detik mulai `b0` dengan potongan mulai `b0 − loop`. Hasil yang benar: seluruh sampel sama.
3. Alasannya: dekoder bersifat deterministik, sehingga badan loop yang diputar ulang harus menghasilkan bit yang sama dengan putaran pertama.
   Hanya sekitar 2.000 sampel pertama setelah sambungan boleh berbeda sedikit (decoder baru dimulai; selisih maksimum yang terukur 0,08 pada trek menu).

### 8.3 Contoh log nyata

**Awal trek BGM yang sehat (stage m012):**
```
[ 388235 ms] ngs2.parse_file path=/app0/nativePS4/sound/bgm/m012/bgm_m012.sspr offset=4944435 ... loop=503136..3831135 blocks=2 b0{...samples=3830880} b1{... rep=4294967295 skip=352 samples=3328000}
[ 388235 ms] ngs2.calc_block type=0x40 ... position=7999 samples=3830880 -> ... extra_args=...,3328000
[ ...      ] ngs2.start rack=0x1000/23 voice=0 ... streaming=1 ...
[ 470422 ms] ngs2.block_end rack=0x1000/23 voice=1 played_ms=79643 dropped_chunks=1 queued=2 next_samples=3328000 next_skip=352 final=0
[ 539730 ms] ngs2.block_end rack=0x1000/23 voice=1 played_ms=148977 dropped_chunks=0 queued=3 next_samples=3328000 next_skip=352 final=0
```
`queued=2` dan `queued=3` pada `block_end` berarti chunk loop sudah siap: sambungan mulus (PASS). `queued=0` berarti terlambat: kemungkinan SEAM-GAP.

**Pola macet (sebelum perbaikan):**
```
[ 663426 ms] ngs2.at9_waiting_for_data rack=0x1000/23 voice=0 rc=-2113929215 ... head=7665722e04000000 ... at9_byte=1152   (head = teks "ver.", bukan ATRAC9)
[ 663426 ms] ngs2.starved rack=0x1000/23 voice=0 played_ms=83392 queued=3
...  (queued tumbuh sampai 45, voice tak pernah pulih; 337 detik)
```
`head=` menunjukkan 8 byte pertama dari superframe yang gagal; byte yang terbaca sebagai teks berarti decoder membaca memori yang bukan data musik.

**Jalur pemulihan baru:**
```
[ 670045 ms] ngs2.stream_begin_late rack=0x1000/23 voice=2 samples=5678798 skip=256 was_final=1 wait=0
```

### 8.4 Membaca kode game

Untuk memahami apa yang diminta game (dipakai saat menyelidiki musik loop pada dump yang diuji, CUSA01159):

- Alamat tamu = `0x800000000` + offset di image `eboot` yang diekstrak. Contoh:
  - *feeder* stream (mengirim chunk berulang tanpa henti) pada `0x800e02980`;
  - parameter id `0x10000001` (blok waveform) dibangun di `0x800e02b5e` dan `0x800e032e9`.
- Cara melihat: ekstrak image lalu `objdump -D -b binary -mi386:x86-64 -M intel --adjust-vma=0x800000000`.
- Untuk alamat host di dump crash/hang: `llvm-symbolizer --obj=basara4-core.exe --relative-address` dengan alamat dikurangi alamat dasar modul.

Alamat ini khusus untuk dump yang diuji; dump atau patch lain bisa berbeda.

### 8.5 Daftar perintah cepat

```powershell
python tools\audio_session_report.py $U         # temuan otomatis
python tools\audio_loop_check.py $U             # loop musik (butuh WAV capture)
python tools\audio_master_check.py $U           # puncak/clipping mix (butuh WAV capture)
python tools\audio_log_summary.py "$U\audio_debug.log"
Select-String "$U\audio_debug.log" -Pattern 'ngs2\.block_end'
Select-String "$U\audio_debug.log" -Pattern 'ngs2\.(resync|starved|refilled)'
Select-String "$U\audio_debug.log" -Pattern 'audioout\.stats' | Select-Object -Last 3
```

### 8.6 Riwayat perbaikan audio

| Commit | Perbaikan | Bagian |
|---|---|---|
| `66131a4` | starvation buffer cutscene, unpause saat pergantian stream, pacing multi-port | [4.1](#41-pacing-dan-starvation-buffer-66131a4) |
| `ab1f30e`, `9bf1bf2` | `calc_block`: offset akhir, perataan superframe tanpa offset dobel | [4.2](#42-calc_block-offset-akhir-dan-perataan-superframe-ab1f30e-9bf1bf2) |
| `55ceee4` | limiter lunak tanh | [4.3](#43-limiter-lunak-55ceee4) |
| `1d496a7` | submix basah dibisukan, flag lanjutan, antrean 64 | [4.4](#44-submix-basah-dibisukan-flag-lanjutan-antrean-64-blok-1d496a7) |
| `f194524` | voice kembar mono dipan kiri/kanan | [4.5](#45-voice-kembar-mono-dipan-kiri-dan-kanan-f194524) |
| `9fd4c98` → `2acbd18` | pengaman BGM/dialog, dicabut | [4.11](#411-perubahan-yang-dicabut-9fd4c98-dicabut-2acbd18) |
| `935798e` | observabilitas: identitas voice, lokasi pemanggil, capture WAV | [2](#2-alat-dan-berkas-bukti) |
| `d234359` | blok pengganti tidak memutar; Play bukan restart | [4.6](#46-mengganti-blok-tidak-menjalankan-voice-play-pada-stream-berjalan-bukan-restart-d234359-disempurnakan-3e63e72) |
| `27d03e2` | stream selesai pada chunk akhir | [4.7](#47-stream-selesai-pada-chunk-akhir-27d03e2-disempurnakan-a923b5d) |
| `697bb08` | event 1 menghentikan voice | [4.8](#48-event-1-menghentikan-voice-697bb08) |
| `a923b5d` | musik loop, batas blok dihormati | [4.9](#49-musik-loop-a923b5d) |
| `c7ba3a0`, `3e63e72` | stream baru dimulai bersih, Play bila antrean kosong, sinkron ulang | [4.10](#410-awal-stream-yang-terlambat-decoder-bersih-dan-sinkron-ulang-c7ba3a0-3e63e72) |
