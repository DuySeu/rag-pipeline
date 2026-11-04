# Hướng dẫn tích hợp rag-pipeline vào app

Tài liệu mô tả cách chuyển ingestion và retrieval của repo này sang một app chạy thật: chuyển những file nào, đổi nguồn tài liệu từ thư mục local sang S3 hoặc MinIO, lưu `file_hash` vào bảng database, và đổi vector store từ Chroma sang Qdrant.

Giả định: app viết bằng Python, có Postgres, và gọi được OpenRouter. Nếu app dùng ngôn ngữ khác thì phần "Chuyển những file nào" không áp dụng; logic cần port lại vẫn là các mục còn lại. Các đoạn code là phác thảo, chưa chạy với version thư viện cụ thể nào.

## Chuyển những file nào

| File | Thuộc pipeline | Vai trò | Phụ thuộc cần đổi khi migrate |
| --- | --- | --- | --- |
| `ingestion/loaders.py` | ingestion | Đọc `.md`, `.txt`, `.docx`, `.xlsx`, `.pptx`, `.pdf` thành `Section` | Loader nhận `Path` (`ingestion/loaders.py:144`) |
| `ingestion/chunking.py` | ingestion | Các chiến lược cắt chunk `fixed`, `recursive`, `heading` | Không |
| `ingestion/corpus.py` | ingestion | Liệt kê file, load, hash từng file | Dùng `rglob` và `read_bytes` trên thư mục local (`ingestion/corpus.py:18-76`) |
| `ingestion/pipeline.py` | ingestion | Chunk id, `plan_changes`, điều phối ingest tăng dần hoặc rebuild | Gọi API của Chroma (`ingestion/pipeline.py:114-176`) |
| `retrieval/pipeline.py` | retrieval | Embed câu hỏi, query, gọi chat model, parse citation | `collection.query` của Chroma và ngưỡng `MAX_DISTANCE` theo distance (`retrieval/pipeline.py:30`, `retrieval/pipeline.py:76`) |
| `core/openrouter.py` | dùng chung | Client OpenRouter, embedder, map lỗi API | Không |
| `core/store.py` | dùng chung | Mở, kiểm tra, xoá, tạo lại collection Chroma | Viết lại cho Qdrant |
| `core/config.py`, `core/constants.py` | dùng chung | `Settings`, giá trị mặc định | Thay `chroma_dir` bằng cấu hình Qdrant, S3, database |
| `core/errors.py` | dùng chung | `ErrorKind`, `CliError` | Map sang lỗi của app (mục "Lỗi và exit code") |
| `tests/` của các module trên | | Test không gọi mạng, dùng `fake_embed` | Đổi fixture `settings` theo `Settings` mới |

Ingestion cần `core/` cộng `ingestion/`. Retrieval cần `core/` cộng `retrieval/pipeline.py`. Hai phía không import lẫn nhau, chỉ chung vector store (và từ giờ chung bảng database).

Dependency runtime cho cả hai pipeline: `openai`, `openpyxl`, `pypdf`, `python-docx`, `python-pptx` (ingestion), cộng client của vector store. Migrate theo tài liệu này thêm `boto3`, `qdrant-client`, và driver Postgres mà app đang dùng; bỏ `chromadb`.

## Thứ tự migrate

Mỗi bước đổi một thứ và có cách kiểm tra riêng.

1. Chuyển code sang app, vẫn dùng thư mục local và Chroma. Kiểm tra: test pass.
2. Đổi nguồn tài liệu sang S3/MinIO. Kiểm tra: ingest hai lần liên tiếp, lần hai không embed gì.
3. Chuyển `file_hash` và cấu hình store sang bảng Postgres.
4. Đổi Chroma sang Qdrant. Kiểm tra: so kết quả retrieval của cùng một bộ câu hỏi trước và sau.
5. Gắn ingestion vào job, retrieval vào endpoint, xử lý chạy đồng thời.

## Nguồn tài liệu: thư mục local sang S3 hoặc MinIO

### Code hiện tại phụ thuộc thư mục local ở đâu

- `list_documents` quét `docs_dir.rglob("*")` (`ingestion/corpus.py:18-23`).
- `file_hashes` đọc toàn bộ byte từng file để tính sha256 (`ingestion/corpus.py:59-63`).
- Mỗi loader nhận một `Path` (`ingestion/loaders.py:144-152`).
- Chunk id và `source` là path tương đối so với `docs_dir` (`ingestion/pipeline.py:51`). Golden set dùng cùng kiểu path.

`plan_changes`, logic xoá và thêm chunk, dấu dirty không đụng tới file nên không phải đổi.

### Cách 1: sync về thư mục local (khuyên dùng cho bước đầu)

Chạy `aws s3 sync s3://<bucket>/<prefix> <docs_dir>` (MinIO: `mc mirror`) ngay trước ingest. Không đổi dòng code nào; lệnh sync chỉ tải object đã đổi. Chi phí: cần ổ đĩa trên máy chạy job, và một bước chạy trước ingest.

### Cách 2: đọc thẳng từ bucket

S3 và MinIO dùng chung `boto3`; với MinIO chỉ cần thêm `endpoint_url`. Credential đọc từ biến môi trường chuẩn của AWS (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`), không ghi vào config.

Thay `file_hashes(docs_dir)` bằng một hàm liệt kê object và trả về cùng kiểu `{source: hash}`, với `source` là key bỏ đi prefix để chunk id và golden set không đổi:

```python
# List supported objects under the prefix as {source: etag}, source being the key without the prefix
def object_hashes(s3: S3Client, bucket: str, prefix: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            source = obj["Key"].removeprefix(prefix).lstrip("/")
            if Path(source).suffix.lower() in LOADERS and not Path(source).name.startswith("~$"):
                hashes[source] = obj["ETag"].strip('"')
    return dict(sorted(hashes.items()))
```

Dùng ETag làm `file_hash` để khỏi tải cả object chỉ để hash. Ba điều cần biết:

- ETag chỉ bằng MD5 của nội dung khi object được upload một lần (single-part) và không mã hoá SSE-KMS; upload multipart cho ETag khác MD5 (https://docs.aws.amazon.com/AmazonS3/latest/API/API_Object.html). ETag vẫn đổi khi nội dung đổi. Upload lại cùng nội dung với cách chia part khác có thể đổi ETag, gây embed lại thừa một file, không sai dữ liệu.
- Đổi từ sha256 sang ETag làm mọi file thành "đã sửa" ở lần chạy đầu, embed lại toàn bộ một lần.
- MinIO có tính ETag giống S3 với upload multipart hay không: chưa kiểm.

Loader vẫn cần nội dung file. Cách ít sửa nhất là tải các file `new` và `changed` vào một thư mục tạm (`tempfile.TemporaryDirectory`, `s3.download_file`) giữ nguyên cấu trúc key, rồi gọi `load_documents(tmp_dir, only=...)` như hiện tại. Loader không phải sửa.

## Bảng database lưu `file_hash`

### Vì sao chuyển ra bảng

Hiện `file_hash` nằm trong metadata của từng chunk và cấu hình build nằm trong metadata collection. Chuyển ra Postgres giải quyết ba giới hạn của cách đó:

- File không ra chunk nào (PDF toàn ảnh) không có chỗ lưu hash nên bị load lại mỗi lần. Bảng lưu được dòng với `chunk_count = 0`.
- Dấu dirty hiện là một cờ cho cả store, chết giữa chừng là rebuild toàn bộ. Bảng có trạng thái theo từng file, chỉ cần làm lại file đang dở.
- Qdrant có lưu metadata tuỳ ý ở cấp collection hay không: chưa kiểm. Bảng không phụ thuộc vào việc đó.

Nếu vẫn giữ hash trong payload của Qdrant mà không thêm bảng thì logic hiện tại port sang được nguyên vẹn; bảng là nâng cấp, không bắt buộc cho bước 4.

### Schema

```sql
-- Settings the active vector collection was built with; one row per logical store
CREATE TABLE vector_store_config (
    store_name       TEXT PRIMARY KEY,      -- alias retrieval queries, e.g. 'docs'
    collection_name  TEXT NOT NULL,         -- physical Qdrant collection behind the alias
    embed_model      TEXT NOT NULL,
    chunk_strategy   TEXT NOT NULL,         -- '<strategy>-v<CHUNKING_VERSION>'
    chunk_size       INTEGER NOT NULL,
    chunk_overlap    INTEGER NOT NULL,
    passage_prefix   TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per source file that has been ingested into a store
CREATE TABLE ingested_files (
    store_name   TEXT NOT NULL REFERENCES vector_store_config (store_name),
    source       TEXT NOT NULL,             -- same value as the chunk payload 'source'
    file_hash    TEXT NOT NULL,             -- sha256 (local) or ETag (S3/MinIO)
    chunk_count  INTEGER NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('pending', 'done')),
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store_name, source)
);
```

Các cột trong `vector_store_config` là đúng các khoá `collection_metadata()` đang ghi (`ingestion/pipeline.py:93-103`), trừ `corpus_hash`. `corpus_hash` không cần nữa vì bảng `ingested_files` đã là danh sách đầy đủ.

### Luồng ingest với bảng

1. Đọc `vector_store_config` của store. Không có dòng, hoặc lệch cấu hình hiện tại: full rebuild (mục "Full rebuild không làm gián đoạn retrieval").
2. `stored = {source: file_hash}` từ `ingested_files` có `status = 'done'`. Dòng `pending` không đưa vào `stored`, nên `plan_changes` coi file đó là `new` và làm lại. Đây là chỗ thay cho dấu dirty `corpus_hash = ""`.
3. `plan = plan_changes(stored, current)`, giữ nguyên hàm hiện có.
4. Load, chunk, embed các file `new` và `changed`. Lỗi ở đây thì chưa ghi gì.
5. Với từng file trong `changed`, `new`, `removed`: upsert dòng với `status = 'pending'` và hash mới (file `removed` giữ hash cũ), xoá point cũ theo `source`, upsert point mới, rồi set `status = 'done'` và `chunk_count` (file `removed` thì xoá dòng).
6. File không ra chunk nào được ghi `status = 'done'`, `chunk_count = 0`, nên lần sau không bị load lại.

## Vector store: Chroma sang Qdrant

### Những chỗ đang gọi Chroma

| Vị trí | Lệnh Chroma | Thay bằng Qdrant |
| --- | --- | --- |
| `core/store.py:14-66` | `PersistentClient`, `get_or_create_collection`, `delete_collection`, `create_collection` | `QdrantClient(url=..., api_key=...)`, `collection_exists`, `create_collection(vectors_config=VectorParams(size=..., distance=Distance.COSINE))`, `delete_collection` |
| `ingestion/pipeline.py:115` | `collection.get(include=["metadatas"])` | Đọc `ingested_files` (hoặc `scroll` qua payload nếu không dùng bảng) |
| `ingestion/pipeline.py:150` | `delete(where={"source": {"$in": [...]}})` | `delete(points_selector=FilterSelector(filter=Filter(must=[FieldCondition(key="source", match=MatchAny(any=[...]))])))` |
| `ingestion/pipeline.py:169-176` | `add(ids, documents, metadatas, embeddings)` | `upsert(points=[PointStruct(id=..., vector=..., payload={...})])`, text nằm trong payload |
| `ingestion/pipeline.py:148`, `:155` | `modify(metadata=...)` | Ghi `vector_store_config` trong Postgres |
| `retrieval/pipeline.py:76-80` | `query(query_embeddings, n_results, include)` | `query_points(query=vector, limit=top_k, with_payload=True).points` |
| `core/store.py:30-45` (`check_collection`) | `count()`, `metadata` | `count()` của Qdrant cộng đọc `vector_store_config` |

### Những khác biệt dễ làm hỏng retrieval

- **Point id:** Qdrant chỉ nhận id là số nguyên không dấu hoặc UUID, không nhận chuỗi `leave.md#0`. Dùng `uuid.uuid5(NAMESPACE, chunk_id)` làm id (ổn định qua các lần chạy) và lưu `chunk_id` gốc trong payload.
- **Score và distance ngược chiều:** Chroma trả cosine distance (`1 - similarity`, càng nhỏ càng gần), Qdrant với `Distance.COSINE` trả similarity (càng lớn càng gần). `RetrievedChunk.distance` và `within_threshold` đang giả định distance (`retrieval/pipeline.py:26-30`, `retrieval/pipeline.py:95-100`). Đổi thành `distance = 1 - point.score` ở `retrieve` để phần còn lại giữ nguyên ý nghĩa.
- **Kích thước vector:** Qdrant cần `size` khi tạo collection. Lấy `len(vectors[0])` sau khi embed, trước khi tạo collection, đúng chỗ code hiện tại gọi `recreate_collection` sau `embed`.
- **Index payload:** tạo `create_payload_index(field_name="source", field_schema=PayloadSchemaType.KEYWORD)` để lệnh xoá theo `source` không phải quét toàn bộ.
- **Không có transaction:** Qdrant không gộp delete và upsert thành một thao tác nguyên tử. Trạng thái `pending` trong `ingested_files` là thứ bảo đảm làm lại được.

### Chuyển dữ liệu đang có

Hai lựa chọn:

- Ingest lại từ đầu vào Qdrant: đơn giản, nhưng embed lại toàn bộ corpus, tốn quota OpenRouter.
- Script một lần: đọc `collection.get(include=["embeddings", "documents", "metadatas"])` từ Chroma, upsert sang Qdrant với id `uuid5`, và điền `ingested_files` từ `source`, `file_hash` trong metadata. Không tốn quota. Khuyên dùng cách này nếu nguồn tài liệu chưa đổi sang S3 cùng lúc; nếu đã đổi sang ETag thì đằng nào cũng embed lại toàn bộ một lần.

Sau khi chuyển, chạy cùng một bộ câu hỏi trên cả hai store và so chunk trả về. Chunk và vector giống nhau nên kết quả phải gần như không đổi; chênh lệch nhỏ có thể đến từ việc HNSW là tìm kiếm gần đúng.

### pgvector thay cho Qdrant

Nếu app đã có Postgres, pgvector cho phép xoá chunk cũ, thêm chunk mới và cập nhật `ingested_files` trong cùng một transaction, nên không cần trạng thái `pending`. Đổi lại là tốc độ query và vận hành gắn với database chính. Chọn khi corpus nhỏ và app chưa có Qdrant.

## Chạy trong app

### Ingestion là job, không phải request

`ingest(docs_dir, settings, embed)` chạy đồng bộ, gọi API embed và có thể mất nhiều phút. Chạy nó trong worker hoặc job theo lịch (hoặc trigger từ event upload của bucket), không chạy trong request HTTP.

Hai lần ingest chạy cùng lúc sẽ ghi đè nhau. Giữ một lock cho mỗi store, ví dụ `pg_try_advisory_lock(hashtext(store_name))` của Postgres; không lấy được lock thì bỏ qua lần chạy đó.

### Full rebuild không làm gián đoạn retrieval

Code hiện tại drop collection rồi tạo lại (`core/store.py:58-66`), điều này đúng vì repo giả định ingestion và retrieval không chạy cùng lúc. Trong app, retrieval vẫn phục vụ khi ingestion chạy, nên drop collection làm query lỗi trong lúc rebuild. Dùng alias của Qdrant:

1. Retrieval luôn query qua alias (`store_name`, ví dụ `docs`).
2. Full rebuild ghi vào collection mới (ví dụ `docs_<timestamp>`), alias vẫn trỏ collection cũ.
3. Xong thì trong một transaction Postgres thay `vector_store_config.collection_name` và toàn bộ dòng `ingested_files` của store.
4. Chuyển alias sang collection mới (`update_collection_aliases`), rồi xoá collection cũ.
5. Lần ingest sau, nếu alias không trỏ đúng `collection_name` trong bảng (chết giữa bước 3 và 4) thì chuyển alias trước khi làm gì khác.

Với lần chạy tăng dần, giữa lúc xoá point cũ và upsert point mới của một file, query có thể không thấy file đó trong một khoảng ngắn. Chấp nhận được ở corpus nhỏ; nếu không chấp nhận được thì upsert point mới trước rồi mới xoá point cũ không còn trong danh sách id mới.

### Retrieval là endpoint

`ask(question, collection, embed, client, settings)` (`retrieval/pipeline.py:175-187`) dùng client `openai` đồng bộ. Tạo `OpenAI` client và Qdrant client một lần khi app khởi động rồi dùng lại; trong framework async (FastAPI), gọi `ask` qua threadpool hoặc chuyển sang `AsyncOpenAI` và client async của Qdrant.

`Answer.kind` (`answer`, `clarify`, `refuse`) trả về như một field của response, không dùng HTTP status cho chúng vì cả ba đều là kết quả hợp lệ.

### Lỗi và exit code

| `ErrorKind` (`core/errors.py`) | Nghĩa | Gợi ý trong app |
| --- | --- | --- |
| `CONFIG` (5) | Thiếu key, key sai, store rỗng hoặc lệch model | HTTP 500 cho endpoint; job dừng và báo động |
| `RATE_LIMIT` (6) | 429 theo phút | HTTP 503 kèm `Retry-After`; job retry có backoff |
| `QUOTA` (7) | Hết quota ngày hoặc hết credit | HTTP 503; job dừng tới ngày UTC kế tiếp |
| `API` (8) | Lỗi khác từ OpenRouter | HTTP 502; job retry giới hạn số lần |

## Cấu hình và secret

`Settings` (`core/config.py:17-31`) đổi như sau. Secret đọc từ biến môi trường, không ghi vào file config hay database.

| Bỏ | Thêm | Nguồn |
| --- | --- | --- |
| `chroma_dir` | `qdrant_url`, `qdrant_api_key` | config, env (`QDRANT_API_KEY`) |
| `collection` | `store_name` (alias) | config |
| `docs_dir` | `s3_bucket`, `s3_prefix`, `s3_endpoint_url` (để trống với AWS, URL của MinIO) | config; credential từ env AWS chuẩn |
| | `database_url` | env (`DATABASE_URL`) |

Giữ nguyên `chunk_strategy`, `chunk_size`, `chunk_overlap`, `top_k`, `embed_model`, `chat_model` (lấy giá trị từ `ModelId` trong `core/constants.py`), `OPENROUTER_API_KEY`. Đổi bất kỳ giá trị nào trong nhóm cấu hình build (`embed_model`, chunk, `PASSAGE_PREFIX`) vẫn kéo theo full rebuild, giờ được phát hiện qua `vector_store_config`.

## Kiểm tra sau mỗi bước

Test hiện có chuyển cùng module, đổi fixture `settings` trong `tests/conftest.py` theo `Settings` mới. Test Qdrant chạy được không cần server với `QdrantClient(":memory:")`; test S3 dùng một thư mục tạm thay cho bucket ở tầng `object_hashes` hoặc một mock S3 (chọn cái app đang dùng).
