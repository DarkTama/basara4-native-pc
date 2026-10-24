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

- File yang **sudah dilacak** harus bersih (tidak ada baris ` M`). Baris `??` boleh ada.
- Jangan pernah `git add .` atau `git add -A`: masih ada folder yang sengaja tidak dilacak (misalnya `.agents/` dan beberapa skrip lama di `tools/`).
  Tambahkan file dengan nama yang jelas saja.
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

## 4. Uji paket di folder bersih (jangan dilewati)

1. Ekstrak `dist/basara4-windows.zip` ke folder kosong yang baru, misalnya `C:\Temp\b4test` (bukan folder game Anda yang sehari-hari).
2. Buka `Sengoku Basara 4.exe`. Judul/pojok launcher harus menampilkan **v1.0.1**.
3. Salin dump game ke `game\` (atau pakai **Browse**), tekan **PLAY**. Start pertama menyiapkan image sekitar satu menit.
4. Masuk sampai menu utama, masuk satu stage, dengar musiknya (loop setelah sekitar 80 detik), lalu keluar normal.
5. Cek bahwa tidak ada `user\crash\`.
6. Hapus folder uji.

Kalau launcher tidak naik atau ada DLL yang hilang di mesin lain, jangan lanjut ke langkah 5.

## 5. Commit, tag, dan push

Pastikan semua perubahan yang ingin dirilis sudah di-commit (`git status --short` tidak menampilkan ` M`).

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
