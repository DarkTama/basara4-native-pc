# Sengoku Basara 4 Sumeragi untuk PC (Basara 4 PC)

[English](README.md) | **Bahasa Indonesia**

Port PC native open-source dan launcher untuk **Sengoku Basara 4 Sumeragi** (PlayStation 4, CUSA01159) untuk Windows 10/11. Kode x86-64 asli game berjalan langsung di CPU PC Anda tanpa emulasi. Library sistem PlayStation 4 diimplementasikan ulang fungsi demi fungsi ke dalam C/C++ native, dan perintah GPU diterjemahkan ke Vulkan 1.3 secara real-time.

> **Tidak menyertakan file game berhak cipta.** Anda wajib menyediakan salinan dump legal dari konsol PlayStation 4 milik sendiri (teruji: CUSA01159).
>
> Proyek ini adalah proyek rekayasa balik (reverse-engineering) dan preservasi open-source independen.
> Tidak berafiliasi dengan, didukung oleh, atau terkait dengan Capcom maupun Sony Interactive Entertainment.
> "Sengoku Basara" adalah merek dagang milik Capcom Co., Ltd. "PlayStation" adalah merek dagang milik Sony Interactive Entertainment.

## Fork
Repositori ini adalah fork dari Basara 4 PC karya Razz ([razzconnect/basara4-native-pc](https://github.com/razzconnect/basara4-native-pc)), dibangun di atas SpartanPort, bloodborne_pc, dan shadPS4; kredit lengkap di [NOTICE.md](NOTICE.md).

## Spesifikasi Sistem
- **Sistem Operasi**: Windows 10 64-bit (1903 ke atas) / Windows 11 64-bit
- **Prosesor (CPU)**: CPU 64-bit dengan dukungan AVX (Intel Sandy Bridge 2011+, AMD FX)
- **Kartu Grafis (GPU)**: GPU yang mendukung Vulkan 1.3 (GTX 1060 / Radeon RX 580)
- **RAM**: 8 GB RAM
- **Penyimpanan**: Ruang kosong ~20 GB
- **File Game**: Salinan dump legal dari Sengoku Basara 4 Sumeragi (`CUSA01159`)
Linux tidak didukung secara native; menjalankan build Windows melalui Proton/Wine belum diuji.

## Cara Pasang & Memainkan
1. Unduh `basara4-windows.zip` dari https://github.com/DarkTama/basara4-native-pc/releases lalu ekstrak.
2. Salin folder dump game PS4 Anda ke dalam `game\`, atau klik tombol **Browse** di launcher untuk memilih lokasinya.
3. Jalankan **`Sengoku Basara 4.exe`**, pilih pengaturan Anda, dan klik **PLAY**. Peluncuran pertama akan menyiapkan image binary ke `out\` (memakan waktu sekitar 30–60 detik); peluncuran berikutnya langsung instan.
4. Untuk langsung bermain dengan konfigurasi tersimpan tanpa membuka GUI launcher, jalankan **`Play Sengoku Basara 4.cmd`**.

## Patch Translasi Bahasa Inggris
Repositori ini **tidak menyertakan teks terjemahan atau aset game**. Pengguna wajib menyediakan patch translasi bahasa Inggris versi PS3 **BLJM61248** (v0.85 + Matabe fix).

1. Dapatkan file patch translasi PS3 (BLJM61248) (`patch 0.85/rom` dan `matabe fix/pl035`).
2. Buka launcher, klik **"Import PS3 English Patch…"**, lalu pilih folder atau file `.zip` patch PS3 Anda.
3. Repatcher akan memverifikasi file patch dengan manifes hash (`data/ps3_patch_manifest.json`), mengekstrak tabel teks GMD, mengonversi tekstur UI ke format native PS4, dan mengemas ulang arsip ke `mods/english_patch/`.
4. Centang opsi **"English Translation Patch (PS3 v0.85 Port)"** kapan saja untuk menerapkan patch via overlay sparse (`user/app0_overlay`) tanpa mengubah file asli game.

Percakapan saat bertarung dan dialog cerita tidak pernah diterjemahkan dalam patch PS3 asli sehingga tetap berbahasa Jepang.

```bash
python launcher/basara4_repatch.py "mods/ps3 english patch" --game game/CUSA01159 --out mods/english_patch
```

## Kontrol
- **Insert** (Keyboard) atau **R3 + L2** (Gamepad): Membuka menu overlay dalam game (pengaturan FSR, anisotropic filtering, status controller).
- **F12**: Mengambil screenshot ke folder `user\`.
- Controller native XInput & SDL3 (Xbox, DualShock 4, DualSense, Switch Pro) dikenali secara otomatis. Untuk gamepad generik / DirectInput, klik **Map controller** di launcher untuk kalibrasi tombol dan stik analog menggunakan `basara4-pad-mapper.exe`.

## Membangun dari Source Code
Prasyarat: MSYS2 dengan environment CLANG64 dan Python 3.10+ di Windows.

```bash
pacman -S --needed base-devel mingw-w64-clang-x86_64-toolchain \
    mingw-w64-clang-x86_64-cmake mingw-w64-clang-x86_64-ninja \
    mingw-w64-clang-x86_64-vulkan-headers mingw-w64-clang-x86_64-vulkan-loader \
    mingw-w64-clang-x86_64-sdl3 mingw-w64-clang-x86_64-ffmpeg \
    mingw-w64-clang-x86_64-glslang mingw-w64-clang-x86_64-pkg-config
```

```powershell
python -m pip install pyinstaller pillow
```

Langkah kompilasi:
```bash
git clone --recursive https://github.com/DarkTama/basara4-native-pc.git
cd basara4-native-pc
bash build.sh
```

```powershell
python packaging/basara4/make_package.py --out dist/basara4-windows
```

## Log
- `user\last_run.log`: Log diagnostik runtime utama (inisialisasi, kompilasi shader GPU, crash traceback).
- `user\prepare.log`: Output dari tahap persiapan dan relokasi image.
- `user\audio_debug.log`: Detail alokasi suara Ngs2, streaming feed, dan status buffer.

## Kredit
- **Razz** - Basara 4 PC port
- **Felipe Prado** - SpartanPort
- **Supermedo & deadinside28** - bloodborne_pc
- **shadPS4 team** - GPU core
- **Mystechry / MikoMango & indrasundanese** - PS3 English patch

Pemberitahuan dan lisensi lengkap: [NOTICE.md](NOTICE.md).

## Lisensi
Dirilis di bawah lisensi GNU General Public License v2.0 atau lebih baru (GPL-2.0-or-later).
Lihat [LICENSE](LICENSE) dan [NOTICE.md](NOTICE.md).
