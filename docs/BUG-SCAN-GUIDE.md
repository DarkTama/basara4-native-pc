# Panduan memindai bug sendiri: audio dan video (universal)

Panduan ini untuk **menemukan dan memilah bug apa pun** di port ini tanpa perlu tahu penyebabnya lebih dulu: suara, gambar, hang, crash, atau yang belum pernah terjadi.
Hasil akhirnya satu paket bukti dan satu laporan singkat yang cukup untuk diperbaiki. Dua dokumen lain menjelaskan perbaikan yang sudah dibuat dan cara memeriksa masing-masing:
[AUDIO-VERIFICATION.md](AUDIO-VERIFICATION.md) dan [FIXING-METHOD.md](FIXING-METHOD.md).

## Isi

1. [Alur enam langkah](#1-alur-enam-langkah)
2. [Persiapan perekaman (sekali)](#2-persiapan-perekaman-sekali)
3. [Saat bermain: apa yang dicatat](#3-saat-bermain-apa-yang-dicatat)
4. [Pindai otomatis setelah bermain](#4-pindai-otomatis-setelah-bermain)
5. [Pohon keputusan: dari gejala ke alat](#5-pohon-keputusan-dari-gejala-ke-alat)
6. [Gejala audio](#6-gejala-audio)
7. [Gejala video](#7-gejala-video)
8. [Game hang atau crash](#8-game-hang-atau-crash)
9. [Mempersempit: ubah satu hal saja](#9-mempersempit-ubah-satu-hal-saja)
10. [Baseline sehat: pesan yang boleh diabaikan](#10-baseline-sehat-pesan-yang-boleh-diabaikan)
11. [Paket bukti dan templat laporan](#11-paket-bukti-dan-templat-laporan)
12. [Yang tidak tercakup](#12-yang-tidak-tercakup)

Dalam contoh perintah, `$U` adalah folder `user\` game Anda, dan alat bantu (`tools\...`) dijalankan dari **folder repositori** (paket pemain tidak memuatnya):

```powershell
cd <folder repositori>
$U = "C:\Games\basara4-windows\user"        # ganti dengan folder user\ game Anda
```

---

## 1. Alur enam langkah

| # | Langkah | Hasil |
|---|---|---|
| 1 | **Rekam** (nyalakan perekam sebelum main) | log, opsional WAV, opsional rekaman layar |
| 2 | **Catat** jam dinding setiap hal yang janggal saat main | daftar "jam + apa + di mana" |
| 3 | **Pindai otomatis** setelah game ditutup | daftar temuan tanpa perlu tahu kapan terjadinya |
| 4 | **Persempit**: cocokkan jam, lihat sekitar jam itu, ubah satu variabel | jenis bug dan area (audio, GPU, UI, kode game) |
| 5 | **Kumpulkan paket bukti** | folder kecil yang bisa dikirim |
| 6 | **Laporkan** dengan templat | gejala, jam, build, bukti |

Aturan emas: **satu sesi, semua bukti.** Bermain 15 sampai 20 menit mahal. Jangan mulai sebelum perekam menyala, dan jangan menutup game sebelum jam gejala dicatat.

---

## 2. Persiapan perekaman (sekali)

### 2.1 Tingkat perekaman

| Tingkat | Nyalakan | Ruang disk | Bisa mendeteksi |
|---|---|---|---|
| **Dasar** | tidak ada, hanya `user\last_run.log` dan catatan Windows | kecil | crash, hang, reset GPU, kegagalan GPU |
| **Audio log** | launcher: centang **Audio debug log** | maks 64 MB | voice macet, kegagalan dekode, loop musik, event penting |
| **Audio penuh** | + centang **WAV capture (up to 3 GB)** | sampai **3 GB per sesi** | hening di mix, clipping, loop identik, isi voice |
| **Pengembang** | jalankan lewat `Debug run.bat` (skrip lokal milik pengembang; **tidak ada di repositori maupun paket**, jadi tingkat ini hanya untuk mesin pengembang) | + ratusan MB (rekaman layar, frame) | tangkapan frame cutscene, rekaman layar, `report.txt` otomatis |

> **Ruang dan privasi.** WAV capture memakai 70 sampai 130 MB per menit (3 GB maksimum), berlaku untuk **satu** kali start, lalu mati sendiri.
> Rekaman layar dari `Debug run.bat` menangkap **seluruh layar utama**: tutup jendela pribadi, atau set `DEBUG_NO_VIDEO=1`.
> Hapus `user\audio_wav\`, `user\recordings\`, dan `user\framedump\` sesudah selesai.

### 2.2 Sebelum mulai

1. Tutup aplikasi yang memakai GPU atau audio (peramban video, perekam layar lain).
2. Catat jam mulai (dan versi: `v1.0.1` di pojok launcher, atau nama `checkpoint` bila memakai binary lama).
3. Catat jumlah event reset GPU saat ini supaya bisa dibandingkan nanti:
   ```powershell
   (Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; Id=153} -ErrorAction SilentlyContinue | Measure-Object).Count
   ```
   (khusus NVIDIA; di AMD/Intel lihat catatan driver di Event Viewer.)

---

## 3. Saat bermain: apa yang dicatat

Cukup satu baris per kejadian, **dengan jam dinding** (log menyimpan jam, dan jam itu yang menghubungkan gejala dengan bukti):

```
JAM   Di mana (menu / stage / cutscene apa / dialog)   Apa yang terlihat atau terdengar   Berlangsung berapa lama   Pulih sendiri atau tidak
01:15 stage m012, setelah kamera berpindah ke cutscene  musik mati total                   sampai akhir stage         tidak pulih
```

Aturan:

- Tulis **gejala**, bukan tebakan penyebab.
- Bila bisa, sebut cutscene atau trek yang sedang tampil/terdengar.
- Dengan `Debug run.bat`: tekan **F9** saat gejala masih berlangsung (merekam frame dan analyzer). Jangan menutup game dulu.
- Setelah gejala, lanjutkan bermain beberapa menit supaya terlihat apakah pulih; lalu **tutup normal** (menu keluar atau tombol close, bukan Task Manager) supaya laporan otomatis dibuat.
- Game **hang** (tidak merespons): jangan tutup dulu. Lompat ke [bagian 8](#8-game-hang-atau-crash).

---

## 4. Pindai otomatis setelah bermain

Jalankan **berurutan**. Tiap perintah punya hasil yang diharapkan; yang tidak sesuai adalah temuan.

```powershell
cd <folder repositori>
$U = "C:\Games\basara4-windows\user"        # ganti dengan folder user\ game Anda
$log = "$U\last_run.log"; $alog = "$U\audio_debug.log"
```

### 4.1 Kegagalan keras (semua harus kosong atau 0)

```powershell
Select-String $log  -Pattern 'DEVICE LOST|Assertion|Crash dump|Unreachable|still does not fit' -CaseSensitive
Get-ChildItem "$U\crash" -ErrorAction SilentlyContinue                                  # dump crash
(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; Id=153; StartTime=[datetime]'2026-10-25 01:00'} -ErrorAction SilentlyContinue | Measure-Object).Count
```
(ganti tanggal/jam di `StartTime` dengan jam mulai sesi Anda; hasilnya harus 0.)

### 4.2 Pesan baru di log game (pemindai universal)

Perintah ini mengelompokkan semua peringatan dan kesalahan setelah angka-angka dinormalkan, sehingga pesan yang berulang tampil satu kali dengan jumlahnya:

```powershell
Select-String $log -Pattern '<(Warning|Error|Critical)>' | ForEach-Object {
    ($_.Line -replace '\[\d\d:\d\d:\d\d\]','[T]' -replace '0x[0-9a-fA-F]+','0x#' -replace '\d+','N')
} | Group-Object | Sort-Object Count -Descending | Select-Object -First 40 | ForEach-Object { "{0,5}  {1}" -f $_.Count, $_.Name }
```

**Cara membaca:** bandingkan dengan [baseline sehat (bagian 10)](#10-baseline-sehat-pesan-yang-boleh-diabaikan). Pesan **di luar baseline** atau **jumlahnya meledak** adalah temuan.
Kata kunci yang paling bernilai: `<Error>`, `<Critical>`, `DEVICE LOST`, `Assertion`, `exceeds`, `clamped`, `cannot serve it`, `redirected`, `not supported`.

### 4.3 Audio

```powershell
python tools\audio_session_report.py $U            # temuan otomatis: SILENT-VOICE, STUCK-STARVED, DECODE, RESTART, TWIN, MIX-GAP, CLIP, FAULT
python tools\audio_loop_check.py $U                # loop musik (butuh WAV capture)
python tools\audio_master_check.py $U              # puncak dan clipping mix (butuh WAV capture)

Select-String $alog -Pattern 'ngs2\.resync|bad_chunk|at9_decode_failed|at9_waiting_for_data|at9_init_failed|at9_no_|blocks_truncated|wav\.budget_reached'   # harus kosong
Select-String $alog -Pattern 'audioout\.stats' | ForEach-Object { if ($_.Line -match 'buffers=(\d+)') { [int]$Matches[1] } } | Measure-Object -Minimum -Maximum -Average   # 900 sampai 990
Select-String $alog -Pattern 'silent_calls=[1-9]'                                                                                                                       # harus kosong
```
Arti temuan `audio_session_report.py` dan kriteria lengkapnya: [AUDIO-VERIFICATION.md, bagian 2.4 dan 5](AUDIO-VERIFICATION.md#24-alat-bantu-folder-tools).

### 4.4 Video

```powershell
Select-String $log -Pattern 'image view range|view of depth/stencil image|Barrier range|resolved to the depth image|cannot serve it' |
    ForEach-Object { ($_.Line -replace '^.*?(image view range|view of depth/stencil image|Barrier range|resolved to the depth image|cannot serve it).*$','$1') } |
    Group-Object | Select-Object Name, Count
```
Baris-baris ini adalah **pengaman yang bekerja**; boleh ada. `still does not fit` tidak boleh ada. Kriteria lengkap: [FIXING-METHOD.md, bagian 9](FIXING-METHOD.md#9-prosedur-uji-grafis-lengkap-satu-sesi).

Lalu **lihat gambarnya**: bila memakai `Debug run.bat`, buka PNG di `$U\framedump` (cutscene) dan bagian "cutscenes and debug captures" di `$U\report.txt`. Periksa warna (merah/hijau/ungu?), kotak kecil di pojok, dan bagian yang hilang.

### 4.5 Ringkasan keputusan

| Hasil | Artinya |
|---|---|
| 4.1 semua kosong, 4.2 hanya baseline, 4.3 dan 4.4 bersih | tidak ada bug yang terdeteksi alat. Gejala yang Anda lihat tapi tidak terdeteksi tetap bug: lanjut ke [bagian 5](#5-pohon-keputusan-dari-gejala-ke-alat) |
| ada temuan | catat jenis dan jamnya, lanjut ke bagian 5 dan 9 |

---

## 5. Pohon keputusan: dari gejala ke alat

```
Game masih berjalan?
├── Tidak (hilang dari layar / kembali ke desktop)  → CRASH   → bagian 8.2
├── Ya, tapi tidak merespons / layar beku           → HANG    → bagian 8.1
└── Ya, merespons, tapi ada yang salah
      ├── yang salah adalah SUARA                   → bagian 6
      ├── yang salah adalah GAMBAR                  → bagian 7
      └── yang salah adalah KONTROL / LOGIKA GAME   → catat jam dan lokasi; periksa 4.2 untuk pesan baru; bagian 9; laporkan (di luar alat audio/video)
```

Untuk setiap gejala, tiga pertanyaan menentukan arah:

1. **Terjadi terus atau sesekali?** Terus → konfigurasi atau kode jalur umum. Sesekali → keadaan balapan atau data tertentu (trek, cutscene).
2. **Terjadi di tempat yang sama?** Ya → kaitkan dengan trek atau cutscene (nama berkas ada di log). Tidak → suspek sumber daya (memori, GPU, jam).
3. **Pulih sendiri?** Ya → antrean atau pengaman bekerja; tidak → macet (state), prioritas tinggi.

---

## 6. Gejala audio

Semua jam di log audio adalah **jam log** (ms sejak proses mulai). Ubah jam dinding ke jam log: `jam log = jam dinding − waktu proses mulai`.
Cara tercepat: lihat `timeline.log` (dari `Debug run.bat`), atau `(Get-Process basara4-core).StartTime` saat game masih berjalan.

| Gejala | Kemungkinan | Periksa lebih dulu | Bukti yang dikumpulkan |
|---|---|---|---|
| **Musik hilang total** sejak awal | voice tidak dimulai, routing, port | `audioout.stats` (loud/peak per port), `ngs2.start` untuk voice mayor | log audio, daftar `ngs2.start` |
| **Musik berhenti setelah satu putaran** | loop gagal | `ngs2.block_end ... final=0`, `audio_loop_check.py` | log + WAV voice |
| **Musik mati di tengah dan tidak pulih** | decoder macet | `STUCK-STARVED`, `at9_*`, `ngs2.starved`, `ngs2.resync` | log (sekitar jam gejala), WAV voice |
| **Jeda singkat di sambungan loop** | blok berikutnya terlambat | `audio_loop_check.py` → `SEAM-GAP`; `block_end ... queued=0` | WAV voice |
| **Suara putus-putus / krek** | pacing buffer, kekurangan data | `audioout.stats` `buffers` di luar 900 sampai 990, `silent_calls`; `MIX-GAP` | log, `report.txt` |
| **Suara dobel / gema / flanging** | dua voice kembar atau jalur basah | `TWIN` pada jam gejala; `p1->0x2001` pada baris `ngs2.start` | log + WAV master di jam itu |
| **Dialog terulang / terpotong** | Play diulang, event 1, stream selesai | `RESTART` pada voice mayor, `ngs2.event`, `ngs2.end` | log sekitar jam gejala |
| **Dialog tidak keluar** | voice tidak dimulai atau hening | `SILENT-VOICE`, `ngs2.start` voice itu | log + WAV voice |
| **Volume terlalu keras / pecah** | clipping | `CLIP`, `audio_master_check.py`; `audioout.volume` | WAV master |
| **Suara hanya di satu sisi** | pan atau routing | `TWIN`, `audioout.backend device_channels` | WAV master |
| **Suara tertinggal dari gambar (cutscene)** | pacing | `audioout.stats` stabil? jam `ngs2.start` voice cutscene vs jam cutscene di `timeline.log` | log + timeline |
| **Game hang saat ada suara** | event 1, polling | laju `flag_calls` ([AUDIO 4.8](AUDIO-VERIFICATION.md#48-event-1-menghentikan-voice-697bb08)); bagian 8.1 | log + hasil `hang_all.py` |

**Langkah umum untuk gejala audio apa pun:**

1. Dapatkan jam log gejala (±5 detik).
2. Cetak baris log di jendela itu:
   ```powershell
   Get-Content $alog | Where-Object { $_ -match '^\[\s*(\d+) ms\]' -and [int]$Matches[1] -ge 700000 -and [int]$Matches[1] -le 706000 }   # ganti angka ms
   ```
3. Cari: `ngs2.start` (voice apa dimulai), `ngs2.event`, `ngs2.starved` / `ngs2.refilled`, `ngs2.block_end`, `ngs2.end`, `at9_*`.
4. Bila ada WAV: buka berkas `voice_r<seri>_v<indeks>_<ms>.wav` yang `ms`-nya dekat, dan periksa apakah isinya hening (alat membantu: `audio_session_report.py`).
5. Bandingkan dengan sesi sehat (log baseline) untuk trek yang sama.

Rumus dan tabel angka (panjang putaran, kapan `block_end` seharusnya muncul): [AUDIO-VERIFICATION.md, lampiran 8.1](AUDIO-VERIFICATION.md#81-rumus-dan-konversi).

---

## 7. Gejala video

Cutscene event dikenali dari nama berkasnya di log dan di `timeline.log` (`evp...`, `evm...`); gambar normal tidak berubah.

| Gejala | Kemungkinan | Periksa lebih dulu | Bukti yang dikumpulkan |
|---|---|---|---|
| **Layar hitam, game masih berjalan (suara lanjut)** | frame tidak ditampilkan, tekstur kosong, movie (FMV) tidak tampil | `last_run.log` `<Error>`; baris `avplayer.*` dan `AvPlayer trace` di `timeline.log` (movie yang diminta game) | log, `timeline.log`, F9 |
| **Layar hitam dan game seperti hang** | **perangkat GPU hilang** | event Windows 153 pada jam itu, `DEVICE LOST` di `last_run.log` | event log, `last_run.log`, minidump bila ada |
| **Latar merah / warna salah** | tekstur warna jatuh ke depth image atau format salah | `resolved to the depth image`, `cannot serve it` | PNG framedump, `last_run.log` |
| **Gambar dobel / kotak kecil di pojok** | view dibatasi ke level 0 pada pass downsample | sama dengan baris di atas | PNG framedump, frame analyzer |
| **Tekstur hilang, putih, atau hitam pada objek tertentu** | format tidak didukung, shader gagal | peringatan `Surface format ... not fully supported`, `<Error>` baru | `last_run.log`, F9 (analyzer) |
| **Garis, kedip (flicker), artefak** | sinkronisasi, VSync, barrier | ubah VSync dan mode layar ([9](#9-mempersempit-ubah-satu-hal-saja)); `Barrier range` | rekaman layar atau foto layar, `last_run.log` |
| **FPS turun / patah-patah** | beban CPU/GPU, kompilasi shader pertama | masalah hanya di kali pertama stage? (kompilasi shader); ulangi sesi kedua | catatan jam dan lokasi (tidak ada alat FPS di sini) |
| **Rasio gambar / resolusi salah** | pengaturan launcher | resolusi, FSR 1, mode layar | tangkapan layar launcher |
| **Crash saat menu / pemilihan stage** | masalah UI | lihat [FIXING-METHOD.md bagian 7](FIXING-METHOD.md#7-kasus-4-crash-di-pemilihan-stage) | dump crash, `last_run.log` |

**Langkah umum untuk gejala video apa pun:**

1. Tekan **F9** saat gejala terlihat (dengan `Debug run.bat`). Bila tidak memakai itu, ambil tangkapan layar (F12 di game menyimpan ke `user\`) atau foto layar.
2. Setelah sesi: jalankan [4.1, 4.2, dan 4.4](#4-pindai-otomatis-setelah-bermain).
3. Cocokkan jam gejala dengan baris `[JJ:MM:DD]` di `last_run.log` (peringatan GPU berstempel jam dinding).
4. Bila gambar sebagian benar: bandingkan beberapa frame PNG berurutan; yang berubah dari benar ke salah menunjukkan kapan bug muncul.
5. Cek apakah masalah bergantung pengaturan ([bagian 9](#9-mempersempit-ubah-satu-hal-saja)).

---

## 8. Game hang atau crash

### 8.1 Hang (tidak merespons)

**Jangan tutup game.** Dengan game tetap terbuka:

```powershell
python tools\hang_all.py                # setiap thread: di mana ia menunggu, dengan nama fungsi host (butuh build dengan simbol)
python tools\hang_sample.py --seconds 3 # thread tersibuk dan alamat tamunya
```
Lalu periksa: `DEVICE LOST` / `Unreachable` di `last_run.log`, event Windows 153, apakah `DrawRec` berputar di `Semaphore::Wait` (tanda GPU hilang), atau thread audio.
Kemudian tutup game dan simpan semua bukti.

### 8.2 Crash

1. `Get-ChildItem "$U\crash"` → ada `core-*.dmp`? Simpan.
2. Akhir `last_run.log`: `Get-Content $log -Tail 40` (alamat, pesan pengecualian, tumpukan).
3. Bila dump tidak ada: crash bukan dari penanganan pengecualian kami (mungkin game keluar sendiri). Catat jam dan lokasi.
4. Catatan sistem: `Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2} -MaxEvents 10` untuk kesalahan aplikasi pada jam itu.

> Dump berisi bagian memori game. Kirim hanya kepada orang yang dipercaya.

---

## 9. Mempersempit: ubah satu hal saja

Ubah **satu variabel per sesi**, sesi yang sama, lokasi yang sama.

| Variabel | Cara | Menjawab |
|---|---|---|
| Mode layar | launcher: windowed / borderless / exclusive | masalah presentasi (kedip, hitam) |
| VSync | launcher | kedip, patah-patah |
| Batas FPS | launcher: 60 atau lainnya | gerak terlalu cepat, glitch di FPS tinggi |
| FSR 1 / sharpening / resolusi | launcher | artefak gambar, warna |
| Anisotropic | launcher | tekstur |
| Audio debug log / WAV | launcher | apakah perekaman memengaruhi gejala |
| Saklar audio lama | `SPARTAN_NGS2_NO_LOOP_BLOCKS=1`, `SPARTAN_NGS2_EVENT1_PAUSES=1`, `SPARTAN_NGS2_AUTOPLAY_BLOCKS=1`, `SPARTAN_NGS2_PLAY_RESTARTS=1` | apakah gejala lama kembali ([AUDIO 6](AUDIO-VERIFICATION.md#6-uji-pembanding-mengembalikan-perilaku-lama)) |
| **Versi lama (bisect)** | ganti `bin\basara4-core.exe` dengan salah satu di `checkpoints\` (lihat di bawah) | **sejak versi mana** gejala muncul |

### Mencari sejak kapan bug ada (bisect dengan checkpoint)

Folder `checkpoints\` di folder game Anda (bila Anda menyimpannya) memuat binary dari beberapa titik: `basara4-core-checkpoint-stable-gpu-audio-1.exe` (paling lama),
`...-loop-music-1.exe`, dan `...-loop-music-2.exe` (terbaru). Langkah manual (tidak diotomatisasi dan tidak diuji; tutup game dulu):

1. Salin `bin\basara4-core.exe` ke tempat aman (`basara4-core.exe.simpan`).
2. Salin satu binary checkpoint menimpa `bin\basara4-core.exe`.
3. Ulangi skenario yang sama. Catat apakah gejala muncul.
4. **Kembalikan** `basara4-core.exe.simpan` ke `bin\basara4-core.exe` setelah selesai.

Bila gejala hilang di versi lama tetapi ada di versi baru, bug muncul di antara keduanya (kirim kedua nama checkpoint; riwayat commit-nya terbaca di `git log`).
Binary lama memakai `user\` yang sama; selama ini format simpanan tidak berubah, tetapi cadangkan folder `user\savedata` dulu bila ragu.

---

## 10. Baseline sehat: pesan yang boleh diabaikan

Dari sesi bersih 25 Oktober (17 menit, tanpa gejala), `last_run.log` berisi 453 baris, **52 peringatan dan 2 kesalahan**, semuanya jenis ini. Pesan **di luar** daftar ini adalah calon temuan.

| Pesan (angka diganti `N`) | Sifat |
|---|---|
| `vk_instance.cpp ... Extension VK_AMD_... unavailable`, `VK_EXT_shader_stencil_export unavailable`, `VK_EXT_shader_atomic_float... unavailable` | informasi kemampuan GPU saat start; normal di kartu NVIDIA |
| `vk_instance.cpp CollectImageFormatInfo: Surface format ... not fully supported`, `Depth format ... not fully supported` | informasi format saat start |
| `emit_spirv.cpp Float... denorm flushing is not supported by the GPU` | informasi saat start |
| `avplayer.cpp sceAvPlayerDisableStream: (STUBBED) called` (`<Error>`) | fungsi AvPlayer yang belum diimplementasikan; muncul tiap memutar movie |
| `image_view.cpp ImageView: image view type Color3D is incompatible with image type Color2D` (`<Error>`) | terlihat di sesi bersih; **belum diselidiki**; jangan abaikan bila jumlahnya bertambah atau bersamaan dengan gejala |
| `texture ... resolved to the depth image ...` dan `... cannot serve it; looked up again, now ...` | pengaman tekstur bekerja; dicatat maksimum 16 baris ([FIXING-METHOD 5](FIXING-METHOD.md#5-kasus-2-latar-merah-dan-gambar-dobel)) |

Dari log audio: `RESTART` dan `TWIN` pada rack 22 (efek suara) adalah normal (30 `TWIN`, 7 `RESTART` di sesi acuan); satu `SILENT-VOICE` 2,5 detik di dalam efek 22,5 detik juga normal.
`ngs2.stream_begin_late` dan `audioout.stats` adalah catatan, bukan masalah.

---

## 11. Paket bukti dan templat laporan

### 11.1 Isi paket (kecil, aman dikirim)

```powershell
$U = "C:\Games\basara4-windows\user"        # ganti dengan folder user\ game Anda
$d = "$U\bukti-$(Get-Date -Format yyyyMMdd-HHmm)"; New-Item -ItemType Directory $d | Out-Null
Copy-Item "$U\last_run.log", "$U\audio_debug.log", "$U\report.txt", "$U\timeline.log" $d -ErrorAction SilentlyContinue
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; Id=153; StartTime=(Get-Date).AddHours(-3)} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id | Export-Csv "$d\gpu-events.csv" -NoTypeInformation
```

| Berkas | Kapan perlu | Ukuran |
|---|---|---|
| `last_run.log`, `audio_debug.log`, `report.txt`, `timeline.log` | selalu | beberapa MB |
| PNG di `framedump\` yang berkaitan dengan gejala | gejala gambar | beberapa MB |
| satu atau dua `voice_r*_v*_<ms>.wav` sekitar jam gejala | gejala audio | puluhan MB; potong bila perlu |
| `crash\core-*.dmp` | crash | puluhan MB; **berisi sebagian memori game** |
| rekaman layar sekitar jam gejala | gejala sulit dijelaskan | besar; menangkap seluruh layar |

Jangan kirim seluruh `audio_wav\` (GB).

### 11.2 Templat laporan

```
Versi / build:           (v1.0.1 / nama checkpoint)
Mesin:                   (GPU + driver, Windows, CPU)
Pengaturan launcher:     (mode layar, resolusi, FSR, FPS, VSync)

Gejala (satu kalimat):
Jam dinding:             ± 5 detik
Di mana:                 (stage / cutscene / menu / dialog)
Terjadi:                 sekali / berulang / selalu di tempat yang sama
Pulih sendiri:           ya / tidak
Terakhir lancar:         (build atau sesi sebelumnya)

Hasil pindai otomatis (bagian 4):
  4.1 kegagalan keras:   ...
  4.2 pesan baru:        ...
  4.3 audio:             ...
  4.4 video:             ...
Sudah dicoba (bagian 9): (variabel apa dan hasilnya)
Bukti terlampir:          (daftar berkas)
```

---

## 12. Yang tidak tercakup

- **FPS dan frame time:** tidak ada alat pengukur di sini. Gunakan pengukur pilihan Anda dan catat jam dan lokasi.
- **Kualitas bunyi dan keindahan gambar:** alat mengukur ada atau tidaknya masalah (hening, identik, clipping, merah), bukan "enak atau tidak". Itu penilaian manual.
- **Kontrol, simpanan, dan logika game:** di luar alat audio/video; laporkan dengan templat yang sama.
- **GPU selain NVIDIA:** event 153 dan checkpoint draw khusus NVIDIA. Pemindaian log dan pengamatan visual tetap berlaku, tetapi pengaman GPU belum teruji di AMD/Intel.
- **Hanya satu mesin yang diuji** (Windows, RTX 4070). Angka seperti 900 sampai 990 buffer per jendela berasal dari mesin itu.
- **Batas jumlah baris log:** banyak jenis baris dicatat dengan batas (8, 16, 60, 400, 1500, dan sejenisnya). Bila sebuah baris tidak ada, bisa saja batasnya sudah tercapai. Hitung dulu sebelum menyimpulkan.
