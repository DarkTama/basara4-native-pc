# Panduan Rilis (untuk maintainer)

Langkah dari kode sampai rilis GitHub. Dokumen ini untuk pemilik repositori, bukan untuk pemain. Contoh di bawah memakai versi **1.0.1**;
ganti sesuai versi yang Anda rilis.

> Catatan versi: nomor yang tampil di launcher ada di `launcher/basara4_launcher.py` (`VERSION = "1.0.1"`).
> Bila nomornya diganti, ubah juga judul di `CHANGELOG.md`, `CHANGELOG.id.md`, dan `RELEASE_NOTES_1.0.1.md`.

## 0. Yang dibutuhkan

- Windows 10/11 64-bit.
- **MSYS2** dengan lingkungan **CLANG64** dan paket di bagian *Building from Source* pada `README.md`
  (daftar lengkap ada di `packaging/windows/README.md`).
- **Python 3.10+** untuk Windows, dengan `pyinstaller` dan `pillow`:
  ```powershell
  python -m pip install pyinstaller pillow
  ```
- Git. Pembuatan rilis di GitHub bisa lewat situs web (tidak perlu `gh`).

## 1. Periksa repositori

```powershell
cd <folder repositori>
git status --short
git log --oneline origin/main..main
```

- `git status --short` harus kosong. File lokal yang sengaja tidak di-push (`.agents/`, sisa upstream di `tools/`) disembunyikan lewat `.git/info/exclude`.
- Tetap biasakan `git add` dengan nama file yang jelas, bukan `git add .`.
- `git log` menampilkan commit yang belum ada di GitHub. Itu yang akan terkirim saat push.

## 2. Build runtime

Buka terminal **MSYS2 CLANG64** (bukan PowerShell):

```bash
cd /g/basara4-pc          # sesuaikan dengan lokasi repositori
bash build.sh
```

Hasilnya `out/gpu/basara4-core.exe` (sekitar 140 MB dengan simbol; paketnya nanti di-strip otomatis sekitar 12 MB). Build pertama lama; build ulang jauh lebih cepat.
Tutup dulu game yang sedang berjalan, karena Windows mengunci file `.exe` yang sedang dipakai.

Pastikan build berasal dari commit yang akan dirilis: `git log -1` harus commit terakhir yang Anda mau.

## 3. Rakit paket

Di PowerShell, dari folder repositori:

```powershell
python packaging/basara4/make_package.py --out dist/basara4-windows
```

- Folder `dist/basara4-windows` tidak boleh sudah ada. Hapus dulu, atau pakai `--update` untuk memperbarui folder yang ada
  (`--update` membiarkan `game\`, `user\`, `out\`).
- Skrip ini membangun `Sengoku Basara 4.exe` (PyInstaller), men-strip runtime, menyalin DLL, skrip persiapan, `NOTICE.md`, `LICENSE`,
  kedua `CHANGELOG`, dan README paket. **Isi game tidak pernah ikut.**

Bungkus menjadi zip dan hitung hash:

```powershell
Compress-Archive -Path dist\basara4-windows -DestinationPath dist\basara4-windows.zip -Force
Get-FileHash dist\basara4-windows.zip -Algorithm SHA256
```

Simpan nilai SHA-256 untuk catatan rilis.

**Yang dizip harus `dist\basara4-windows`, jangan folder game yang Anda mainkan sehari-hari.** Folder game Anda berisi `user\` (save, log, `audio_wav` yang bisa
beberapa GB, rekaman, dump) dan `game\` (isi game). Folder `dist\basara4-windows` dari `make_package.py` hanya berisi program dan dokumen; `game\`, `user\`, dan
`out\` di dalamnya kosong. Periksa isi zip sebelum diunggah:

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::OpenRead("$PWD\dist\basara4-windows.zip")
$z.Entries | Where-Object { $_.FullName -match '^basara4-windows[\\/](user|out|game)[\\/]' -and $_.Length -gt 0 } | Select-Object FullName, Length
"{0} berkas, {1:N0} MB tidak terkompresi" -f $z.Entries.Count, (($z.Entries | Measure-Object Length -Sum).Sum / 1MB)
$z.Dispose()
```

Baris `Select-Object` hanya boleh menampilkan `game\PUT_YOUR_GAME_DUMP_HERE.txt` (PowerShell 5.1 menyimpan pemisah folder zip sebagai `\`; versi lain memakai `/`). Kalau ada berkas lain di `user/`, `out/`, atau `game/`, hentikan dan cari sebabnya.
Ukuran tidak terkompresi seharusnya puluhan MB, bukan GB.

## 4. Uji paket di folder bersih (jangan dilewati)

1. Ekstrak `dist/basara4-windows.zip` ke folder kosong yang baru, misalnya `C:\Temp\b4test` (bukan folder game Anda yang sehari-hari).
2. Buka `Sengoku Basara 4.exe`. Judul/pojok launcher harus menampilkan **v1.0.1**.
3. Salin dump game ke `game\` (atau pakai **Browse**), tekan **PLAY**. Start pertama menyiapkan image sekitar satu menit.
4. Masuk sampai menu utama, masuk satu stage, dengar musiknya (loop setelah sekitar 80 detik), lalu keluar normal.
5. Cek bahwa tidak ada `user\crash\`.
6. Hapus folder uji.

Kalau launcher tidak naik atau ada DLL yang hilang di mesin lain, jangan lanjut ke langkah 5.

## 5. Commit, tag, dan push

### Isi repositori yang akan terkirim (sudah diperiksa)

Yang di-push hanya berkas yang dilacak git (**2.230 berkas, sekitar 59 MB**; pustaka pihak ketiga ikut di dalam pohon, bukan submodule yang harus diunduh terpisah).
Satu clone baru dari repositori ini sudah diuji: `cmake` configure berhasil tanpa menyalin apa pun dari luar.

| Folder / berkas | Isi | Ikut push |
|---|---|---|
| `src/` | runtime C: kontrak sistem PS4, mesin audio Ngs2, loader | ya |
| `gpu/` | inti grafis Vulkan (turunan shadPS4) dan pustaka bawaannya di `gpu/third_party/` | ya |
| `third_party/` | LibAtrac9 | ya |
| `launcher/` | launcher (`basara4_launcher.py`, `basara4_patches.py`, ikon) | ya |
| `scripts/` | skrip persiapan image game | ya |
| `packaging/basara4/` | pembuat paket (`make_package.py`), daftar DLL, README paket | ya |
| `packaging/windows/` | berkas resource/manifest Windows yang dipakai CMake | ya |
| `tools/` | alat bantu pengembang (laporan audio, debug_watch, hang_all, save_snapshot, dll.) | ya |
| `tests/` | rangkaian tes bawaan upstream; CMake merujuk berkas di sini | ya |
| `.github/` | templat isu dan dua workflow upstream yang hanya jalan manual | ya |
| `README.md`, `README.id.md`, `CHANGELOG.md`, `CHANGELOG.id.md`, `NOTICE.md`, `LICENSE` | dokumen | ya |
| `RELEASING.md`, `RELEASE_NOTES_1.0.1.md` | panduan ini dan teks rilis | ya |
| `build.sh`, `msys2-env.sh`, `.gitignore`, `.gitattributes`, `.gitmodules` | build dan konfigurasi git | ya |

**Tidak ikut push (sengaja):**

- Diabaikan oleh `.gitignore`: `game/` (isi game), `user/` (save, log, rekaman, dump), `out/` (hasil build), `dist/` (paket), `*.pkg`, `eboot.bin`, `*.mp4`, `*.png` di luar `docs/images/`, dan sejenisnya.
- Disembunyikan hanya di mesin ini lewat `.git/info/exclude` (tidak ada di repositori): `.agents/`, `tools/tools/` (salinan kembar `tools/`),
  dan sisa upstream di `tools/` yang bukan bagian rilis Basara 4 (skrip FSR4, `wad_textures.py`, `zip_mod.py`, `make_ultrawide_patch.py`, dan sejenisnya).
  Isinya tidak dihapus, hanya tidak dilacak. Karena itu `git status` seharusnya kosong.

Catatan: `.github/workflows/windows-zip.yml` dan `linux-appimage.yml` berasal dari upstream dan hanya bisa dijalankan manual (`workflow_dispatch`).
Push dan tag **tidak** memicunya, dan keduanya tidak dipakai untuk rilis ini.

### Periksa sebelum push (semua keluaran harus kosong)

```powershell
git status --short
git ls-files | Select-String -Pattern '(^|/)(game|user|out|dist|checkpoints)/'
git ls-files | Select-String -Pattern 'eboot\.bin|\.(pkg|dmp|wav|mp4|exe|dll|elf|dump)$'
git grep -n -I -E 'C:\\Users|G:\\|ghp_[A-Za-z0-9]{20}|AKIA[0-9A-Z]{12}|BEGIN (RSA|OPENSSH) PRIVATE' -- src launcher scripts packaging tools .github '*.md' ':!RELEASING.md' build.sh
```

Pemeriksaan ini sudah dijalankan pada commit terakhir dan hasilnya kosong semua.

### Commit, tag, push

Pastikan semua perubahan yang ingin dirilis sudah di-commit (`git status --short` kosong).

```powershell
git push origin main

git tag -a v1.0.1 -m "Basara 4 PC 1.0.1"
git push origin v1.0.1
```

- `main` akan mengirim semua commit sejak rilis awal sekaligus (sekitar 30 commit). Itu wajar.
- Tag dibuat **setelah** langkah 4 lolos, dan menunjuk ke commit yang sama dengan yang dibangun. Tag sebelumnya: `v1.0.0`.
- Bila salah menandai tag yang **belum** di-push: `git tag -d v1.0.1`, lalu buat ulang. Setelah di-push, jangan menimpa tag; buat versi baru.

## 6. Buat rilis di GitHub

1. Buka `https://github.com/razzconnect/basara4-native-pc/releases` lalu **Draft a new release**.
2. **Choose a tag**: pilih `v1.0.1`.
3. **Release title**: `Basara 4 PC 1.0.1`.
4. Isi deskripsi dengan isi `RELEASE_NOTES_1.0.1.md` (ganti baris `SHA-256: ...` dengan hash dari langkah 3).
5. Seret `dist/basara4-windows.zip` ke bagian **Attach binaries**.
6. Centang **Set as the latest release**, lalu **Publish release**.

(README menautkan ke halaman Releases dan menyebut `basara4-windows.zip`, jadi nama berkas itu dipertahankan.)

## 7. Setelah rilis

- Unduh zip dari halaman rilis di folder kosong dan jalankan sekali (periksa hash-nya sama).
- Jika ada yang salah pada zip: hapus asetnya di halaman rilis dan unggah ulang. Jika salah pada kode: buat versi baru (1.0.2), jangan menimpa tag.

## Bagian yang sengaja tidak diubah

`README.md` dan `README.id.md` tidak berubah dalam rilis ini. Daftar perbaikan lengkap ada di `CHANGELOG.md` (Inggris) dan `CHANGELOG.id.md`
(Indonesia), dan dilampirkan di paket.
