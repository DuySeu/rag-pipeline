# Kế hoạch implement ingestion tăng dần

Thực hiện theo `docs/design/2026-11-04-incremental-ingestion-design.md`. Không commit. Trước khi bắt đầu, `uv run pytest` cho 90 pass, 1 fail có sẵn (`tests/test_retrieval_eval.py::test_golden_set_sources_exist_in_docs_folder`: golden set trỏ tới `nghi-phep.md` không có trong `data/docs`), không liên quan tới thay đổi này. Chroma đang cài là 1.5.9.

## Bước 1: viết test trước

Thêm các test mới trong mục Test của spec vào `tests/test_ingestion_pipeline.py`, sửa ba test có sẵn theo spec, đổi mọi chỗ gọi `ingest(build_chunks(...), settings, embed, corpus_hash(...))` sang `ingest(docs_dir, settings, embed)` trong `tests/test_ingestion_pipeline.py`, `tests/test_ingestion_loaders.py`, `tests/test_retrieval_pipeline.py`, `tests/test_openrouter.py`. Chạy `uv run pytest` và xác nhận các test này fail vì chưa có code.

## Bước 2: `ingestion/corpus.py`

Thêm `file_hashes`, `corpus_hash_from`; `corpus_hash` gọi hai hàm đó. Thêm tham số `only` cho `load_documents`.

## Bước 3: `core/store.py`

Thêm `find_collection` dùng `list_collections()`.

## Bước 4: `ingestion/pipeline.py`

Thêm `ChangePlan`, `IngestResult`, `plan_changes`. Viết lại `ingest(docs_dir, settings, embed)` theo Data flow của spec, gồm nhánh full rebuild, dấu dirty, và cập nhật `corpus_hash` khi thoát sớm. Gắn `file_hash` vào metadata lúc add. Xoá comment `# ponytail` cũ.

## Bước 5: `main.py`

`run_ingest` gọi `ingest` mới, log tổng kết theo spec, bỏ dòng `Built %d chunks`.

## Bước 6: tài liệu

Cập nhật `README.md` và `CLAUDE.md` theo mục "Tài liệu cần cập nhật" của spec.

## Bước 7: kiểm tra

Chạy `uv run pytest`. Kết quả mong đợi: mọi test pass, chỉ còn đúng test golden set fail như trước. Project không có cấu hình lint hay typecheck riêng nên không chạy thêm.
