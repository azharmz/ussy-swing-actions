# Deploy lifecycle v4

Perubahan ini tidak mengubah scoring, threshold, entry window, holding period,
ATR multiplier, atau RR target. Perubahan hanya membuat pemrosesan candle
idempotent/replayable dan simulasi stop lebih konservatif.

## Urutan deploy wajib

1. Jalankan isi `supabase/migrations/20260831_add_last_processed_date.sql`
   pada Supabase SQL Editor.
2. Jalankan isi `supabase/migrations/20260904_add_mfe_mae.sql` pada Supabase
   SQL Editor.
3. Commit dan push seluruh perubahan repo melalui GitHub Desktop.
4. Jalankan workflow `Daily US Swing Screener` dengan `workflow_dispatch`.
5. Pastikan step `Run lifecycle unit tests` lulus sebelum step screener.

Jangan membalik langkah 1 dan 2. Kode v4 mengirim kolom
`last_processed_date`; run akan gagal bila migration belum diterapkan.
Kode v5 juga mengirim `mfe_pct` dan `mae_pct`, sehingga migration kedua wajib
diterapkan sebelum workflow yang berisi kode v5 dijalankan.

## Perilaku baru

- Rerun pada market date yang sama tidak menambah `days_in_status` lagi.
- Bila satu atau beberapa run gagal, candle yang belum diproses direplay secara
  kronologis pada run berikutnya.
- Jika update sebuah candle gagal, replay berhenti pada sinyal tersebut agar
  candle berikutnya tidak diterapkan keluar urutan.
- Candle pending yang menyentuh entry dan stop sekaligus dihitung konservatif
  sebagai `sl_hit`, bukan dikeluarkan sebagai `missed`.
- Posisi entered yang gap melewati stop diisi pada `min(Open, stop_loss)`.

## Verifikasi setelah run

Query berikut seharusnya tidak menghasilkan baris aktif tanpa tanggal proses:

```sql
select id, ticker, strategy, status
from public.trade_signals
where status in ('pending', 'entered')
  and last_processed_date is null;
```
