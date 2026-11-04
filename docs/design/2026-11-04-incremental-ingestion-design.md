# Thiết kế ingestion tăng dần theo file

Tài liệu mô tả thay đổi đã được người dùng chốt ngày 2026-11-04: `python main.py ingest` chỉ load, chunk và embed các file mới hoặc đã đổi nội dung so với lần ingest trước, xoá chunk của file đã bị gỡ khỏi thư mục docs, và vẫn tự rebuild toàn bộ khi cấu hình build store thay đổi. Luồng vẫn nằm trong ingestion pipeline, không thêm subcommand. Thay đổi này chưa được implement.

## Bối cảnh

Hiện `ingest()` embed toàn bộ chunk rồi gọi `recreate_collection` (drop và tạo lại collection) ở mỗi lần chạy (`ingestion/pipeline.py:77-103`). Comment `# ponytail: full rebuild re-embeds every doc, add per-file hashing if the corpus grows` (`ingestion/pipeline.py:86`) đánh dấu đúng điểm nâng cấp này. `corpus_hash()` đã hash từng file bằng sha256 trên raw bytes nhưng chỉ lưu một hash tổng vào metadata collection (`ingestion/corpus.py:54-59`, `ingestion/pipeline.py:64-73`). Chunk id có dạng `<path relative to docs_dir>#<index>` và metadata mỗi chunk đã có `source` (`ingestion/pipeline.py:51`, `ingestion/pipeline.py:98`).

Ngoài phạm vi: phát hiện đổi tên file, chạy đồng thời với retrieval, file bị sửa trong lúc ingest đang chạy (hash và nội dung load được đọc ở hai lần khác nhau), embed song song hoặc async, đổi vector store.

## Quyết định đã chốt

| Chủ đề | Quyết định | Ảnh hưởng |
| --- | --- | --- |
| File đã ingest nhưng bị xoá khỏi docs | Xoá chunk của file đó khỏi store | Store luôn khớp thư mục docs, `eval-ingestion` vẫn pass |
| Cấu hình build store thay đổi | Tự rebuild toàn bộ, không cần flag | Store không bao giờ trộn vector của hai cấu hình |
| Nơi lưu hash đã ingest | Trường `file_hash` trong metadata của từng chunk | Hash và chunk được ghi cùng một lệnh `add`, không lệch nhau; không có file manifest riêng |
| Cách xác định "đã đổi" | sha256 trên raw bytes của file, cùng cách `corpus_hash` đang làm | Đổi metadata hệ thống (mtime) mà nội dung giữ nguyên thì không embed lại |

Cấu hình build store là các khoá do `collection_metadata()` trả về, trừ `corpus_hash`: `embed_model`, `chunk_size`, `chunk_overlap`, `chunk_strategy` (gồm `CHUNKING_VERSION`), `embed_passage_prefix`.

## Component

### `ingestion/corpus.py`

- Thêm `file_hashes(docs_dir: Path) -> dict[str, str]`: trả về `{relative_posix_path: sha256_hex(raw bytes)}` cho đúng các file `list_documents` trả về.
- `corpus_hash(docs_dir)` dựng lại từ `file_hashes`, giữ nguyên công thức hiện tại (cùng chuỗi `path\0hash\n` theo thứ tự path đã sort) để hash của store cũ không đổi giá trị. Thêm `corpus_hash_from(hashes: Mapping[str, str]) -> str` chứa công thức đó; `corpus_hash(docs_dir)` gọi `corpus_hash_from(file_hashes(docs_dir))`, còn `ingest` gọi thẳng `corpus_hash_from` để không đọc file hai lần.
- `load_documents(docs_dir, only=None)`: tham số `only: Collection[str] | None`. Khi có, chỉ load các path tương đối nằm trong `only`. Warning về file không hỗ trợ vẫn log như cũ.

### `ingestion/pipeline.py`

- `Chunk` và `chunk_documents` giữ nguyên signature (đang được `evaluation/ingestion_eval.py` và ba file test dùng). `file_hash` chỉ được gắn lúc ghi: metadata khi `add` là `{"source": ..., "index": ..., **c.location, "file_hash": hashes[c.source]}` (`file_hash` đặt sau `location` để key của loader không ghi đè).
- `ChangePlan`: dataclass `frozen=True` với bốn tuple path đã sort: `new`, `changed`, `removed`, `unchanged`.
- `plan_changes(stored: Mapping[str, str], current: Mapping[str, str]) -> ChangePlan`: hàm thuần. `new` là path có trong `current` mà không có trong `stored`; `changed` là path có ở cả hai nhưng hash khác; `removed` là path chỉ có trong `stored`; còn lại là `unchanged`.
- `IngestResult`: dataclass `frozen=True` gồm `plan: ChangePlan | None` (`None` khi full rebuild), `embedded_chunks: int`, `stored_chunks: int`, `empty_files: tuple[str, ...]` (file load được nhưng không ra chunk nào).
- `ingest(docs_dir: Path, settings: Settings, embed: Embedder) -> IngestResult` điều phối toàn bộ (xem mục Data flow). Phần ghi batch hiện có (`CHROMA_ADD_BATCH_SIZE`) tách thành hàm nội bộ dùng chung cho full rebuild và incremental.
- Xoá comment `# ponytail` ở dòng 86 vì điều kiện nâng cấp đã xảy ra. Giữ comment "Embed first so a failed API call leaves the previous index untouched".
- `build_chunks` giữ nguyên, vẫn được `tests/test_ingestion_loaders.py` và `tests/test_ingestion_pipeline.py` dùng để kiểm chunking.

### `core/store.py`

- Thêm `find_collection(settings) -> Collection | None`: trả về collection nếu đã tồn tại, `None` nếu chưa, không tạo mới. Kiểm tra tồn tại bằng `list_collections()` giống `delete_collection` (`core/store.py:41-46`), không dựa vào exception của `get_collection`. Cần để phân biệt "chưa có store" với "store rỗng" mà không để `open_collection` tạo collection thiếu metadata.

### `main.py`

- `run_ingest` gọi `ingest(docs_dir, settings, embed)` thay vì tự `build_chunks`; bỏ dòng log `Built %d chunks from %s`. Embedder vẫn tạo trước (tạo client không gọi mạng). `ValueError` từ `ingest` hiện không được bắt trong `run_ingest` (`main.py:38-50`); giữ nguyên hành vi đó, ngoài phạm vi.
- Log tổng kết một dòng. Full rebuild: `Rebuilt store: embedded N chunks`. Incremental: `new=a changed=b removed=c unchanged=d, embedded N chunks`. Không có gì để embed và không có gì để xoá: `Store is up to date, nothing to embed`. Nếu `empty_files` khác rỗng thì log thêm một warning liệt kê các file đó. Sau đó giữ dòng `Stored %d chunks in ...` hiện có.

### Không đổi

`evaluation/ingestion_eval.py` (vẫn so chunk id, `corpus_hash`, config với thư mục docs), `retrieval/`, `config.yaml`.

## Data flow

1. Tính `current = file_hashes(docs_dir)`. Nếu rỗng thì raise `ValueError` "No chunks to ingest" như hiện tại, store không bị đụng.
2. `collection = find_collection(settings)`. Chọn full rebuild nếu một trong các điều kiện sau đúng: `collection is None`; `collection.count() == 0`; metadata collection lệch `collection_metadata(settings, ...)` ở bất kỳ khoá nào trừ `corpus_hash`; metadata collection có `corpus_hash == ""` (dấu dirty của một lần incremental bị dừng giữa chừng, xem bước 4.5); có chunk thiếu `file_hash` trong metadata (store build trước thay đổi này).
3. Full rebuild: load mọi file, chunk, kiểm tra có ít nhất một chunk, embed, `recreate_collection`, add theo batch. Giống luồng hiện tại, chỉ thêm `file_hash`.
4. Incremental:
   1. `stored = {m["source"]: m["file_hash"] for m in collection.get(include=["metadatas"])["metadatas"]}`.
   2. `plan = plan_changes(stored, current)`.
   3. Nếu `new`, `changed`, `removed` đều rỗng: không gọi API, không đụng chunk; chỉ cập nhật metadata (bước 4.9) rồi trả kết quả.
   4. Load và chunk chỉ `new + changed`. Nếu ra ít nhất một chunk thì embed toàn bộ trong một lần gọi `embed`; nếu ra 0 chunk thì không gọi `embed`. Lỗi ở bước này để store nguyên vẹn.
   5. Nếu không có chunk mới và `changed + removed` rỗng (chỉ có file `new` không ra chunk): không đụng chunk; chỉ cập nhật metadata (bước 4.9) rồi trả kết quả. Ngược lại, ghi dấu dirty trước khi sửa store: `collection.modify(metadata=collection_metadata(settings, ""))`.
   6. `collection.delete(where={"source": {"$in": [...changed, ...removed]}})` nếu danh sách khác rỗng.
   7. Add chunk mới theo batch.
   8. `collection.modify(metadata=collection_metadata(settings, corpus_hash_from(current)))`, luôn truyền đủ dict để đúng dù Chroma replace hay merge metadata (chưa kiểm với `chromadb>=1.5`, xác nhận bằng test). Bước này xoá dấu dirty.
   9. Cập nhật metadata khi thoát sớm ở bước 4.3 hoặc 4.5: nếu `corpus_hash` đang lưu khác `corpus_hash_from(current)` thì `collection.modify(metadata=collection_metadata(settings, corpus_hash_from(current)))`. Một lần ghi metadata nên không cần dấu dirty. Cần vì `corpus_hash` tính trên mọi file được liệt kê, kể cả file không ra chunk (thêm hoặc xoá file như vậy không làm đổi `plan` nhưng làm đổi hash).
5. Trả `IngestResult`; `stored_chunks = collection.count()`.

Thứ tự "embed xong mới ghi store" trong gotcha của `CLAUDE.md` được giữ ở cả hai nhánh.

## Xử lý lỗi và edge case

| Tình huống | Hành vi |
| --- | --- |
| Loader lỗi | Dừng trước khi embed, exit 1, store không đổi. Chỉ file `new`/`changed` được load, nên file hỏng nhưng không đổi không chặn lần chạy |
| API embed lỗi | Dừng trước khi ghi store, exit 5-8, message "the previous index is unchanged" vẫn đúng |
| Không có thay đổi | Không gọi API, không ghi chunk, exit 0. Metadata chỉ được ghi khi `corpus_hash` lệch (bước 4.9) |
| Chỉ có file bị xoá | Không gọi API; chỉ `delete` và `modify` metadata |
| Thư mục docs không còn file hỗ trợ | `ValueError` "No chunks to ingest", store giữ nguyên |
| Full rebuild mà mọi file đều không ra chunk | `ValueError` như hiện tại, store giữ nguyên |
| File `changed` không còn ra chunk nào (vd PDF toàn ảnh) | Chunk cũ bị xoá, không add gì; file hiện trong `documents_not_in_store` của `eval-ingestion` như hiện nay |
| File không ra chunk nào ở mọi lần chạy | Là nhánh thoát sớm của bước 4.5, không viết check riêng. Store không lưu được `file_hash` cho file đó, nên mỗi lần ingest nó lại là `new` và bị load lại (không gọi API, có warning). `corpus_hash` vẫn được cập nhật ở bước 4.9 nên `eval-ingestion` không fail vì hash. Chấp nhận chi phí load lại; không thêm bản ghi đánh dấu riêng |
| Xoá một file vốn không ra chunk nào | File không có trong `stored` lẫn `current` nên `plan` rỗng; bước 4.9 cập nhật `corpus_hash` |
| Mọi file đều thành không ra chunk sau một lần incremental | Store còn `count() == 0`; các lần sau đi nhánh full rebuild và raise `ValueError` "No chunks to ingest". Đúng ý, không phải bug |
| Store cũ chưa có `file_hash` | Full rebuild một lần |
| Process chết sau khi ghi dấu dirty và trước bước 4.8 (giữa `delete`, giữa các batch `add`) | Store còn `corpus_hash == ""`, lần chạy sau full rebuild. `eval-ingestion` báo `corpus_hash_mismatch` cho tới lúc đó. Không dựa vào việc file "mất chunk" để tự sửa vì một file có thể đã được add một phần với `file_hash` mới |
| Đổi tên file | Thành `removed` + `new`, embed lại file đó |
| Hai file trùng nội dung | Hash trùng nhưng path khác, xử lý độc lập theo path |

## Test

Tất cả dùng `fake_embed` và fixture `settings`/`docs_dir` trong `tests/conftest.py`, không gọi mạng. Để đếm số text được embed, test bọc `fake_embed` bằng một hàm ghi lại input. Thêm vào `tests/test_ingestion_pipeline.py`:

- `test_plan_changes_splits_new_changed_removed_unchanged`
- `test_second_ingest_embeds_nothing_when_docs_are_unchanged`
- `test_ingest_embeds_only_the_added_file`
- `test_ingest_replaces_chunks_of_an_edited_file` (text cũ không còn, không có chunk id thừa khi file ngắn đi)
- `test_ingest_drops_chunks_of_a_deleted_file`
- `test_ingest_with_only_deletions_makes_no_embed_call`
- `test_ingest_rebuilds_everything_when_chunk_settings_change`
- `test_ingest_rebuilds_store_without_file_hash_metadata`
- `test_ingestion_eval_passes_after_incremental_ingest` (kiểm `corpus_hash` trên metadata được cập nhật)
- `test_incremental_embed_failure_leaves_store_unchanged`
- `test_corpus_hash_is_unchanged_by_refactor` (cùng giá trị với công thức cũ trên `docs_dir`)
- `test_ingest_skips_embed_when_new_file_has_no_chunks` (file rỗng mới thêm: không gọi `embed`, store không đổi, `empty_files` chứa file đó)
- `test_ingestion_eval_passes_after_adding_an_empty_file`
- `test_ingestion_eval_passes_after_deleting_a_file_without_chunks`
- `test_ingest_rebuilds_when_store_is_marked_dirty` (đặt `corpus_hash=""` trên metadata collection, lần ingest sau embed lại toàn bộ)

Khoảng 15 chỗ gọi `ingest(build_chunks(...), settings, fake_embed, corpus_hash(...))` trong `tests/test_ingestion_pipeline.py`, `tests/test_ingestion_loaders.py`, `tests/test_retrieval_pipeline.py`, `tests/test_openrouter.py` đổi sang `ingest(docs_dir, settings, fake_embed)`. Fixture `settings` đã có `chunk_size=40`, `chunk_overlap=10` (`tests/conftest.py:28-29`) nên kết quả chunk không đổi. Đây là hàm nội bộ, không phải public API.

Ba test hiện có cần viết lại có chủ đích, không được làm yếu đi:

- `tests/test_openrouter.py:90-105` (`test_ingestion_exits_with_quota_code_and_keeps_old_index`): sau lần ingest đầu, docs không đổi nên lần chạy thứ hai sẽ không gọi API và exit 0 thay vì 7. Test phải sửa nội dung một file trong `docs_dir` trước khi gọi `main()`, để nhánh incremental thực sự gọi embed và gặp lỗi quota; giữ nguyên assert exit 7 và id không đổi.
- `tests/test_ingestion_pipeline.py:36-41` (`test_ingest_rejects_empty_input_and_keeps_existing_store`): thay `ingest([], settings, fake_embed, "")` bằng `ingest(empty_dir, settings, fake_embed)` với `empty_dir` là một thư mục rỗng dưới `tmp_path`.
- `tests/test_ingestion_pipeline.py:134`: assert metadata chunk đổi thành `[{"source": "a.md", "index": 0, "file_hash": <sha256 của a.md>}]`.

## Tài liệu cần cập nhật

- `README.md`: câu "Ingestion rebuilds the whole collection on each run" đổi thành mô tả hành vi tăng dần và khi nào rebuild toàn bộ.
- `CLAUDE.md`: gotcha đầu tiên nói rõ đổi file trong `data/docs/` chỉ cần chạy `ingest` (tự xử lý tăng dần), còn đổi cấu hình thì `ingest` tự rebuild toàn bộ; gotcha về thứ tự embed rồi mới ghi giữ nguyên.
