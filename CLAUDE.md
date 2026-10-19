# RAG pipeline

Hai pipeline độc lập, chỉ chia sẻ Chroma store trên đĩa (`data/chroma`): `ingestion/` ghi, `retrieval/` đọc. Embedding và chat model đi qua OpenRouter (model free tier). Corpus và golden set là tiếng Việt. Cấu trúc thư mục và định dạng golden set xem README.md.

## Commands

```sh
uv sync
uv run pytest                                              # không gọi API, dùng fake_embed trong tests/conftest.py
uv run ruff check . && uv run ruff format --check .
uv run --env-file .env python -m ingestion                 # rebuild toàn bộ collection
uv run --env-file .env python -m retrieval "<câu hỏi>"
uv run --env-file .env python -m evaluation.ingestion_eval # không gọi API, exit 1 nếu store lệch docs
uv run --env-file .env python -m evaluation.retrieval_eval # hit_rate@k, MRR, RAGAS; chậm, tốn quota
```

## Gotchas

- Đổi `EMBED_MODEL`, `CHUNK_SIZE`, `CHUNK_OVERLAP` hoặc file trong `data/docs/` thì phải chạy lại ingestion. Retrieval phải dùng đúng `EMBED_MODEL` đã build store.
- Ingestion embed xong mới drop collection, nên API lỗi thì index cũ vẫn còn. Giữ nguyên thứ tự này.
- Chunk id có dạng `<path relative to data/docs>#<index>`. `sources` trong golden set dùng cùng kiểu path, và ingestion_eval so khớp theo đó.
- `retrieval_eval` chạy từng sample một để không vượt rate limit free tier, và ghi từng dòng vào `evaluation/reports/retrieval_samples.jsonl` ngay khi xong. Judge RAGAS dùng chính `CHAT_MODEL`.
- `langchain-community<0.4.2` được pin có chủ đích vì ragas 0.4.3 còn import module đã bị xoá. Không nâng version.
- Tiếng Việt: nếu thêm keyword/BM25 search thì không dùng tokenizer/stemmer tiếng Anh.

## Code conventions

- Mỗi function có một dòng `#` comment ở phía trên, không dùng docstring. Dataclass để `frozen=True`. Settings chỉ đọc từ env, qua `rag_core/settings.py`.
- Comment `# ponytail: ...` đánh dấu chỗ cố ý làm đơn giản, kèm điều kiện để nâng cấp. Chỉ nâng cấp khi điều kiện đó xảy ra (eval cho thấy cần), không làm trước.
- Test dùng `fake_embed` và fixture `settings`/`docs_dir` trong `tests/conftest.py`, không gọi mạng.

## Retrieval changes are eval-driven

- Mọi thay đổi chunking, embed model, `TOP_K`, prompt hay cách retrieve đều cần một lần chạy `retrieval_eval` trước và sau. Báo cáo số từ `evaluation/reports/retrieval_summary.json`, không báo "có vẻ tốt hơn".
- Mỗi lần chỉ đổi một biến. Chỉ giữ thay đổi nếu metric mục tiêu tăng và các metric khác không giảm.
- Golden set chỉ có vài câu hỏi, nên chênh lệch nhỏ có thể chỉ là nhiễu. Thêm câu hỏi vào `evaluation/datasets/golden.jsonl` trước khi kết luận.
- Chunk để phục vụ retrieval: khi hit_rate thấp, xem `retrieval_samples.jsonl` để biết câu trả lời có bị cắt ngang ranh giới chunk không, trước khi đổi embed model.
- Re-ranking, hybrid search, async ingestion, đổi vector store: không thêm mặc định. Chỉ đề xuất khi eval chỉ ra retrieval là nút thắt, và nói rõ chi phí (latency, dependency, quota).
- Metric target: (pending), chưa có baseline đo trên corpus này.
