# Catatan Perubahan (Changelog)

[English](CHANGELOG.md) | **Bahasa Indonesia**

Semua perubahan penting Basara 4 PC. Tanggal adalah tanggal commit.

## 1.0.1 - 2026-10-25

Pembaruan pertama setelah rilis awal. Memperbaiki mesin audio (musik yang berhenti, suara dobel atau pecah, suara dialog dan cutscene),
crash GPU serta gambar yang salah di cutscene event, dan crash di pemilihan stage. Juga menambah alat yang menemukan masalah-masalah ini
dari sesi bermain tanpa harus dijelaskan.

### Bagaimana perbaikan ini diperiksa

Semua perbaikan di bawah ditemukan dari log sesi main yang nyata (log debug audio, log game, linimasa dengan jam dinding, rekaman WAV setiap voice
dan hasil mix akhir, tangkapan frame, dan event driver GPU Windows sendiri), bukan dari dugaan. Kalau bisa diukur, diukur: misalnya loop musik
dibandingkan sampel demi sampel dengan putaran pertama.

Tes terakhir adalah satu sesi bermain penuh setelah commit terakhir. Hasilnya tanpa crash, tanpa GPU device loss, tanpa crash dump, dan tanpa voice musik
yang hening. Diuji di **satu mesin saja: Windows, NVIDIA RTX 4070**. Kartu grafis AMD dan Intel belum dicoba.

### Musik dan suara (mesin software Ngs2, `src/runtime_offline.c`)

- **Musik battle loop seperti aslinya.** Dulu satu trek hanya diputar sekali lalu hening. Trek membawa titik loop (chunk `smpl` pada file suara).
  Mesin sekarang melaporkan intro dan loop sebagai dua blok, blok loop berulang tanpa henti, dan aliran ATRAC9 mengikuti batas blok.
  Terukur pada trek yang blok berikutnya datang tepat waktu: putaran kedua dan ketiga identik dengan putaran pertama sampel demi sampel, dan tidak ada klik
  di sambungan. Trek lain juga loop dengan isi yang benar. (`a923b5d`)
- **Musik tidak lagi mati saat trek dimulai dengan urutan yang tidak biasa.** Tiga kasus yang membuat voice musik hening puluhan detik atau selamanya
  diperbaiki: (`c7ba3a0`, `3e63e72`)
  - game mengirim *Play* sebelum chunk pertama trek baru;
  - voice masih milik trek sebelumnya (separuh superframe lama membuat semua superframe trek baru ter-decode bergeser);
  - *Play* pada voice yang antreannya kosong (dulu diabaikan sebagai pengulangan).
  Decoder yang tidak lagi menemukan ATRAC9 yang valid sekarang menyinkronkan diri di awal blok berikutnya.
- **Musik battle mulai di tempat atau ukuran yang salah** (`calc_block` mengembalikan offset akhir di separuh hasil yang salah, lalu frame offset terhitung dua kali):
  diperbaiki dan diselaraskan ke superframe ATRAC9. (`ab1f30e`, `9bf1bf2`)
- **Suara cutscene dan dialog.** Buffer cutscene yang kehabisan data, voice yang tertinggal pause saat stream berganti, dan pacing di beberapa port output
  diperbaiki. (`66131a4`)
- **Voice yang dihentikan event cutscene benar-benar berhenti.** "Event 1" game dulu hanya mem-pause voice, yang lalu tetap "playing" selamanya;
  ini terlihat bersamaan dengan black screen saat cutscene event dimulai. (`697bb08`)
- **Stream yang seluruh waveform-nya sudah di-decode dan tidak punya chunk lagi dianggap selesai**, bukan tetap menjadi voice yang berjalan. (`27d03e2`, `a923b5d`)
- **Mengganti data sebuah voice tidak lagi menjalankannya**, dan *Play* pada stream yang sedang berjalan bukan restart: ditujukan untuk baris dialog yang mulai lagi
  dan musik yang mulai sendiri. (`d234359`)
- **Suara dobel** dari submix basah (port `0x2001`) dibisukan; stream yang berlanjut lewat beberapa chunk didukung dengan antrean blok yang lebih besar. (`1d496a7`)
- **Pasangan stereo.** Voice yang dimulai dua kali pada data yang sama di-pan kiri dan kanan (twin-voice panning sistemik), ditambah pengaman terhadap buffer streaming
  yang kehabisan data. (`f194524`)
- **Clipping digital yang kasar** pada adegan keras diganti limiter `tanh` soft-knee. (`55ceee4`)
- "Pengaman" yang lebih luas dan membuang sebagian dialog (`9fd4c98`) dicabut lagi (`2acbd18`).

### Grafis (inti Vulkan, `gpu/`)

- **Black screen dan hang di cutscene event.** Di NVIDIA, driver kehilangan perangkat GPU ketika quad cutscene membaca depth image (image `D32SfloatS8Uint` 1920x1080)
  yang oleh game dideskripsikan sebagai tekstur warna dengan tiga level mip. Windows mencatatnya sebagai event `nvlddmkm` 153 pada setiap black screen.
  Beberapa perbaikan bersama-sama menghilangkannya:
  - sebuah image view tidak boleh melewati image-nya (level dan layer dibatasi); (`6ef526b`)
  - tabel subresource disesuaikan dengan ukuran image dan rentang barrier dibatasi (memperbaiki assertion barrier Vulkan); (`2acbd18`)
  - view dari image depth/stencil memakai format image itu sendiri dan satu aspek saja; (`0868a14`)
  - pengikatan tekstur yang image-nya tidak bisa memenuhi deskripsi tekstur game (level mip atau layer lebih sedikit dari view, atau format warna pada depth image)
    dicari ulang dengan format yang tepat. (`1fb11ac`)
- **Latar merah dan gambar dobel berukuran separuh di cutscene** berasal dari image yang salah yang sama (depth tampil di kanal merah, salinan hasil downsample
  tergambar di pojok kiri atas). Keduanya hilang dengan perbaikan terakhir di atas; pengaman itu terlihat bekerja dan memilih image yang benar dalam satu sesi tes.
- **GPU yang berhenti merespons tidak lagi membuat game hang tanpa penjelasan.** Menunggu GPU kini melaporkan kesalahan, bukan berputar terus, dan laporannya menyebut
  draw yang menghilangkan perangkat (shader dan image yang dipakainya). Di NVIDIA ini memakai `VK_NV_device_diagnostic_checkpoints` (GPU lain melewatinya). (`b260192`, `a74d38f`)

### Crash

- **Pemilihan stage** tidak lagi crash di mesin markup teks UI MT Framework (issue #1). (`313eef4`)
- **Crash dump ditulis** ke `user\crash\core-<tanggal>-<jam>.dmp` saat proses mati karena fault atau assertion GPU, sehingga laporan bug lebih dari beberapa baris teks. (`c7778ca`)
  *Dump berisi memori game: berikan hanya kepada orang yang Anda percaya.*

### Alat untuk menemukan masalah (repositori sumber, `tools/`)

Tidak ada yang diperlukan untuk bermain. Inilah cara perbaikan di atas ditemukan:

- `audio_session_report.py` membaca sesi bermain dan mendaftar voice hening, voice yang tidak pernah selesai, restart, clipping, dan kesalahan decoder
  tanpa diberi tahu kapan masalahnya terjadi. (`b492459`)
- `debug_watch.py` + `framedump_png.py` menulis linimasa log game, GPU dan audio dengan jam dinding, menangkap frame otomatis saat cutscene event dan lewat **F9**,
  merekam layar per satu menit (NVENC, 30 menit bergulir) dan menulis `user\report.txt` saat game ditutup. *Rekaman menangkap seluruh layar utama*;
  set `DEBUG_NO_VIDEO=1` untuk mematikannya. (`d4b5df5`, `c7778ca`, `6f9756c`)
- `hang_sample.py` dan `hang_all.py` menunjukkan di mana setiap thread game yang berputar berada, lengkap dengan simbol host. (`6ef526b`, `b260192`)
- `save_snapshot.py` menyimpan salinan bernama dari data save, supaya bug yang butuh 20 menit untuk dicapai bisa diulang dari save yang dibuat sebelumnya. (`42bed2e`)
- Log debug audio lebih rinci (identitas voice, lokasi pemanggil, rekaman WAV dari mix dan setiap voice panjang). (`935798e`)

### Keterbatasan yang diketahui

- Bila blok loop berikutnya datang terlambat, musik punya jeda singkat di sambungan (terukur 10 sampai 80 ms, hanya di sebagian trek).
- Hanya grafis NVIDIA yang diuji. Perbaikan GPU mengikuti aturan Vulkan dan tidak seharusnya merugikan kartu lain, tetapi belum terbukti.
- Frame rate di atas 60 tetap eksperimental.
- Saklar lanjutan (variabel lingkungan) bila perubahan musik bermasalah di mesin Anda: `SPARTAN_NGS2_NO_LOOP_BLOCKS=1` mengembalikan musik sekali-putar yang lama,
  `SPARTAN_NGS2_EVENT1_PAUSES=1` penanganan lama untuk event 1.

## 1.0.0 - 2026-10-23

Rilis awal: port PC native dan launcher untuk Sengoku Basara 4 Sumeragi (PS4, CUSA01159).
