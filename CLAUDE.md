# RAG pipeline

Hai pipeline độc lập, chỉ chia sẻ Chroma store trên đĩa (`data/chroma`): `ingestion/` ghi, `retrieval/` đọc. Code dùng chung nằm ở package `core/` (`constants`, `config`, `errors`, `openrouter`, `store`); `main.py` là CLI duy nhất (argparse subcommand), không module nào import `main`. Embedding và chat model đi qua OpenRouter (model free tier). Corpus và golden set là tiếng Việt. Cấu trúc thư mục và định dạng golden set xem README.md.

## Commands

```sh
uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
uv run pytest                                              # không gọi API, dùng fake_embed trong tests/conftest.py
uv run --env-file .env python main.py ingest                # rebuild toàn bộ collection
uv run --env-file .env python main.py ask "<câu hỏi>"
uv run --env-file .env python main.py eval-ingestion        # không gọi API, exit 1 nếu store lệch docs
uv run --env-file .env python main.py eval-retrieval        # hit_rate@k, MRR, RAGAS; chậm, tốn quota
```

## Gotchas

- Đổi `EMBED_MODEL_ID` (`main.py`), `chunk_strategy`, `chunk_size`, `chunk_overlap`, `embed_passage_prefix` hoặc file trong `data/docs/` thì phải chạy lại ingestion. Retrieval phải dùng đúng `EMBED_MODEL_ID` đã build store.
- Ingestion embed xong mới drop collection, nên API lỗi thì index cũ vẫn còn. Giữ nguyên thứ tự này.
- Chunk id có dạng `<path relative to data/docs>#<index>`. `sources` trong golden set dùng cùng kiểu path, và ingestion_eval so khớp theo đó.
- `retrieval_eval` chạy từng sample một để không vượt rate limit free tier, và ghi từng dòng vào `evaluation/reports/retrieval_samples.jsonl` ngay khi xong. Judge RAGAS dùng chính `CHAT_MODEL_ID`.
- `langchain-community<0.4.2` được pin có chủ đích vì ragas 0.4.3 còn import module đã bị xoá. Không nâng version.
- Tiếng Việt: nếu thêm keyword/BM25 search thì không dùng tokenizer/stemmer tiếng Anh.

## Code conventions

- Mỗi function có một dòng `#` comment ở phía trên, không dùng docstring. Dataclass để `frozen=True`. `OPENROUTER_API_KEY` đọc từ `.env`, model embed và chat chọn ở `EMBED_MODEL_ID`, `CHAT_MODEL_ID` trong `main.py` (giá trị lấy từ `ModelId` trong `core/constants.py`) rồi truyền vào `load_settings`, các tham số còn lại từ `config.yaml`, tất cả chỉ qua `load_settings` trong `core/config.py`. Biến `.env` trống thì dừng với exit 5; key `config.yaml` để trống thì lấy `CONFIG_DEFAULTS`.
- Comment `# ponytail: ...` đánh dấu chỗ cố ý làm đơn giản, kèm điều kiện để nâng cấp. Chỉ nâng cấp khi điều kiện đó xảy ra (eval cho thấy cần), không làm trước.
- Test dùng `fake_embed` và fixture `settings`/`docs_dir` trong `tests/conftest.py`, không gọi mạng.

## Retrieval changes are eval-driven

- Mọi thay đổi chunking, embed model, `top_k`, prompt hay cách retrieve đều cần một lần chạy `retrieval_eval` trước và sau. Báo cáo số từ `evaluation/reports/retrieval_summary.json`, không báo "có vẻ tốt hơn".
- Mỗi lần chỉ đổi một biến. Chỉ giữ thay đổi nếu metric mục tiêu tăng và các metric khác không giảm.
- Golden set chỉ có vài câu hỏi, nên chênh lệch nhỏ có thể chỉ là nhiễu. Thêm câu hỏi vào `evaluation/datasets/golden.jsonl` trước khi kết luận.
- Chunk để phục vụ retrieval: khi hit_rate thấp, xem `retrieval_samples.jsonl` để biết câu trả lời có bị cắt ngang ranh giới chunk không, trước khi đổi embed model.
- Re-ranking, hybrid search, async ingestion, đổi vector store: không thêm mặc định. Chỉ đề xuất khi eval chỉ ra retrieval là nút thắt, và nói rõ chi phí (latency, dependency, quota).
- Metric target: (pending), chưa có baseline đo trên corpus này.
