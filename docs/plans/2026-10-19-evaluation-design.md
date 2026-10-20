# Thiết kế đánh giá

Trạng thái: hoãn, làm sau khi xong ingestion và retrieval. Người dùng chốt tập trung vào hai pipeline trước; tài liệu này giữ thiết kế đánh giá đã thống nhất để làm sau. Nó áp dụng spec đánh giá của người dùng ở `docs/benchmark.md` (dữ liệu, ground truth, hai tầng retrieval và generation, metric, hiệu chỉnh judge, metric tuỳ chọn) vào repo này; khi tài liệu này và `docs/benchmark.md` khác nhau, `docs/benchmark.md` là nguồn gốc. Những gì hai pipeline phải sinh ra để đánh giá được nằm ở `docs/plans/2026-10-19-ingestion-pipeline-design.md` và `docs/plans/2026-10-19-retrieval-pipeline-design.md`.

Trong lúc hoãn, quy tắc "retrieval changes are eval-driven" của `CLAUDE.md` vẫn giữ nguyên, nên giá trị `max_distance`, việc đổi `top_k`, `evidence_overlap_min` và mọi target metric là (pending) cho tới khi eval chạy được.

## Quyết định đã chốt

| Chủ đề | Quyết định của người dùng | Ảnh hưởng tới thiết kế |
| --- | --- | --- |
| Thời điểm | hoãn tới khi xong ingestion và retrieval | mọi mục dưới đây chưa implement |
| Metric mục tiêu | độ chính xác của câu trả lời cuối cùng | `answer_accuracy` theo key-fact recall; metric khác là metric bảo vệ |
| Spec đánh giá | corpus cố định, ground truth theo schema, hai lớp, judge được hiệu chỉnh | toàn bộ tài liệu này |
| Phủ đủ evidence | đo việc lấy về đủ mọi evidence cho câu nhiều evidence | `all_evidence_hit@k` cạnh `recall@k` |
| Thành phần golden set | khoảng 10 câu cho mỗi loại trong 6 loại, phủ mọi định dạng file | mục "Loại câu hỏi và thành phần golden set" |
| Judge | khác `CHAT_MODEL` | setting `JUDGE_MODEL`; model cụ thể chưa chọn |
| Hiệu chỉnh judge | người dùng sẽ gán nhãn tay 50-100 mẫu | mục "Judge và hiệu chỉnh" |
| Clarify | pipeline sẽ có cơ chế hỏi lại | `clarify_accuracy`, `false_clarify_rate` |

## Câu hỏi làm rõ và câu trả lời từ code

| Câu hỏi | Trả lời | Nguồn |
| --- | --- | --- |
| Eval hiện đo gì? | `hit_rate@k`, MRR theo file, 4 metric RAGAS; judge là chính `CHAT_MODEL` | `evaluation/retrieval_eval.py:28`, `evaluation/retrieval_eval.py:46-55`, `evaluation/retrieval_eval.py:66-72` |
| Golden set hiện có trường gì? | `question`, `reference`, `sources` | `evaluation/golden.py:10-15`, `evaluation/golden.py:24-25` |
| RAGAS 0.4.3 có metric nhận danh sách key fact không? | Không. Có `FactualCorrectness`, `AnswerCorrectness`, `AnswerAccuracy`, nhưng các metric này tự tách claim từ `reference`, không nhận danh sách fact cho trước | `.venv/lib/python3.12/site-packages/ragas/metrics/collections/__init__.py:17-18`, `.venv/lib/python3.12/site-packages/ragas/metrics/collections/__init__.py:37` |
| RAGAS 0.4.3 có sinh testset không? | Có `TestsetGenerator` với `generate_with_langchain_docs(documents, testset_size, query_distribution=...)` và các synthesizer single-hop, multi-hop | `.venv/lib/python3.12/site-packages/ragas/testset/synthesizers/generate.py:55`, `.venv/lib/python3.12/site-packages/ragas/testset/synthesizers/generate.py:114-121`, `.venv/lib/python3.12/site-packages/ragas/testset/synthesizers/__init__.py:24-32` |

## Hiện trạng

`retrieval_eval` chạy tuần tự từng câu qua `retrieve` và `generate_answer` (`evaluation/retrieval_eval.py:80-115`), tính `hit_rate@k` và MRR theo file (`evaluation/retrieval_eval.py:34-42`, `evaluation/retrieval_eval.py:46-55`) và 4 metric RAGAS với judge là `CHAT_MODEL` (`evaluation/retrieval_eval.py:66-72`). Mỗi dòng ghi ngay vào `retrieval_samples.jsonl` (`evaluation/retrieval_eval.py:117-119`), summary ghi khi mọi câu xong (`evaluation/retrieval_eval.py:121-122`). Không có metric nào so câu trả lời với `reference`, không có trích dẫn, không ghi phiên bản corpus, latency hay token.

## Dữ liệu: corpus cố định

Corpus đánh giá là `data/docs` tại một phiên bản cố định; nó vừa là nguồn để retrieve vừa là căn cứ chấm Faithfulness. Phiên bản được nhận diện bằng `corpus_hash` (hash của path tương đối và nội dung mọi file trong `data/docs`) do ingestion tính và ghi vào metadata collection, cùng `embed_model`, `chunk_size`, `chunk_overlap`, `chunk_strategy` (xem ingestion doc). `retrieval_eval` đọc các giá trị này từ store và ghi vào `retrieval_summary.json` cùng `golden_hash` (hash của `golden.jsonl`).

Hai lần chạy chỉ so sánh được khi `corpus_hash` và `golden_hash` giống nhau. Cấu hình chunk được phép khác khi chính nó là biến đang thử. Nếu `corpus_hash` của store khác hash tính lại từ `data/docs` lúc chạy eval, `retrieval_eval` dừng và yêu cầu chạy lại ingestion.

## Loại câu hỏi và thành phần golden set

Mục tiêu khoảng 10 câu cho mỗi loại, tổng khoảng 60 câu. Thay cho thành phần 3 loại chốt ở vòng trước; câu một source tương ứng `factoid`, câu nhiều source tương ứng `multi_hop` và `comparison`. Tên loại viết kiểu snake_case như ví dụ `"type": "multi_hop"` trong `docs/benchmark.md:24`.

| `metadata.type` | Mô tả | `expected_behavior` | Số câu mục tiêu | Hiện có |
| --- | --- | --- | --- | --- |
| `factoid` | một sự kiện, một đoạn evidence | `answer` | khoảng 10 | 6 (cần chuyển schema) |
| `multi_hop` | phải nối hai đoạn evidence trở lên | `answer` | khoảng 10 | 0 |
| `comparison` | so sánh hai thực thể hoặc hai quy định | `answer` | khoảng 10 | 0 |
| `table` | câu trả lời nằm trong bảng (`.xlsx`, bảng trong `.docx`/`.pptx`/`.pdf`) | `answer` | khoảng 10 | 0 |
| `not_in_corpus` | thông tin không có trong corpus, gồm cả câu cùng chủ đề | `refuse` | khoảng 10 | 0 |
| `ambiguous` | có nhiều cách hiểu hợp lý với context lấy về | `clarify` | khoảng 10 | 0 |

Các câu `answer` phải phủ mọi định dạng file có trong corpus thật (`.md`, `.txt`, `.docx`, `.xlsx`, `.pptx`, `.pdf`), kể cả câu mà câu trả lời chỉ nằm trong speaker notes của `.pptx`. Phân bố chính xác theo định dạng là (pending) tới khi có corpus thật. Số đoạn evidence của một câu không nên vượt `top_k`, nếu không `all_evidence_hit@k` không bao giờ đạt.

## Schema ground truth

Mỗi dòng của `evaluation/datasets/golden.jsonl` là một object:

```json
{
  "qid": "q_001",
  "question": "Nhân viên chính thức có bao nhiêu ngày phép năm?",
  "user_context": null,
  "metadata": {"type": "factoid", "difficulty": "easy", "domain": "nghi-phep"},
  "expected_behavior": "answer",
  "gold_evidence": [{"doc_id": "nghi-phep.md", "page": null, "span": "Nhân viên chính thức được 12 ngày phép năm có lương."}],
  "reference_answer": "Nhân viên chính thức có 12 ngày phép năm có lương, cứ đủ 5 năm làm việc được cộng thêm 1 ngày.",
  "key_facts": ["12 ngày phép năm có lương", "đủ 5 năm làm việc được cộng thêm 1 ngày"]
}
```

Ví dụ trên chỉ minh hoạ định dạng; span lấy nguyên văn từ `data/docs/nghi-phep.md`. File thật ghi mỗi object trên một dòng.

| Trường | Quy ước trong repo này | Dùng cho metric |
| --- | --- | --- |
| `qid` | chuỗi duy nhất dạng `q_001` như `docs/benchmark.md:21`, không đổi khi sửa câu hỏi | khoá nối các lần chạy |
| `question` | tiếng Việt | mọi metric |
| `user_context` | tuỳ chọn, `null` hoặc `{role, permissions}`; corpus hiện không có phân quyền | Access violation rate (N/A) |
| `metadata` | `type` theo bảng loại câu hỏi; `difficulty` là `easy`/`medium`/`hard`; `domain` là chủ đề tự do | tách nhóm trong báo cáo |
| `expected_behavior` | `answer`, `refuse` hoặc `clarify` | Refusal Accuracy, False Refusal Rate, Clarify Accuracy |
| `gold_evidence[].doc_id` | path tương đối so với `data/docs`, giống `source` của chunk (quy ước `CLAUDE.md`) | Recall@k, Precision@k, MRR, Citation P/R |
| `gold_evidence[].page` | số trang với `.pdf`, tên sheet với `.xlsx`, số slide với `.pptx`, `null` với `.md`/`.txt`/`.docx` | như trên |
| `gold_evidence[].span` | đoạn nguyên văn trong tài liệu chứa evidence | Recall@k, Precision@k, MRR |
| `reference_answer` | câu trả lời chuẩn; với `refuse` là câu từ chối cố định; với `clarify` là câu hỏi lại mong đợi | Answer Correctness (kiểm tra mâu thuẫn), Clarify Accuracy |
| `key_facts` | danh sách fact ngắn phải có trong câu trả lời; rỗng với `refuse`; với `clarify` là các cách hiểu cần nêu ra | Answer Correctness, Clarify Accuracy |

Evidence được gán ở mức tài liệu cộng đoạn văn, không theo chunk id, vì chunk id `<path>#<index>` đổi mỗi khi đổi chunking. Câu `refuse` có `gold_evidence: []`.

Kiểm tra trong `load_golden`: `qid` không trùng; `expected_behavior` thuộc 3 giá trị; `answer` phải có `gold_evidence` và `key_facts` khác rỗng; `refuse` phải có `gold_evidence` rỗng; `doc_id` phải tồn tại dưới `data/docs` theo path tương đối. Test `tests/test_retrieval_eval.py:33-36` hiện so bằng `p.name` ở cấp đầu; đổi sang kiểm tra `(docs_dir / doc_id).is_file()` để đúng với thư mục con. Việc span có khớp được chunk nào không do `ingestion_eval` kiểm tra (xem ingestion doc).

## Chuyển từ golden set cũ

6 câu hiện có chuyển sang schema mới bằng tay: `qid` mới, `type` là `factoid`, `expected_behavior` là `answer`, `doc_id` lấy từ `sources`, `page` là `null`, `reference_answer` lấy từ `reference`, còn `span` và `key_facts` phải viết tay từ tài liệu. Format cũ không được giữ song song; `load_golden` chỉ đọc schema mới. README mục "Golden set format" phải cập nhật theo.

## Quy trình xây ground truth

Theo `docs/benchmark.md:45-51`, áp vào repo này:

1. Sinh câu hỏi từ corpus bằng framework. Chọn RAGAS `TestsetGenerator.generate_with_langchain_docs` (`.venv/lib/python3.12/site-packages/ragas/testset/synthesizers/generate.py:114-121`), sinh câu hỏi, câu trả lời chuẩn và đoạn context nguồn. Đầu vào là một LangChain `Document` cho mỗi section mà lớp loader của ingestion sinh ra, metadata gồm `doc_id` và `page`. Tỉ lệ single-hop và multi-hop đặt qua `query_distribution` với `SingleHopSpecificQuerySynthesizer`, `MultiHopSpecificQuerySynthesizer`, `MultiHopAbstractQuerySynthesizer` (`.venv/lib/python3.12/site-packages/ragas/testset/synthesizers/__init__.py:24-32`); tỉ lệ cụ thể là (pending). Kết quả ghi vào `evaluation/datasets/golden_draft.jsonl`, eval không đọc file này.
2. Map context nguồn về `gold_evidence`: `doc_id` và `page` lấy từ metadata của `Document` mà framework dùng để sinh câu hỏi; `span` là đoạn text trong context đó chứa câu trả lời.
3. Tách key facts: dùng LLM tách `reference_answer` thành danh sách `key_facts`.
4. Bổ sung câu khó: tự viết, hoặc dùng LLM sinh rồi kiểm tra, các câu không có trong tài liệu (`refuse`) và câu mơ hồ (`clarify`). Framework thường không sinh hai loại này.
5. Người review: kiểm tra câu hỏi tự nhiên, câu trả lời đúng với tài liệu; loại câu quá dễ (chép nguyên văn từ tài liệu). Dùng model khác với model của hệ thống RAG đang được đánh giá: mọi bước dùng LLM ở trên (1, 3, 4) chạy bằng `JUDGE_MODEL`, không dùng `CHAT_MODEL`. Chỉ câu đã review mới được chép vào `golden.jsonl`.

Bổ sung riêng cho repo này, ngoài 5 bước của spec: RAGAS 0.4.3 không có synthesizer riêng cho câu `comparison` và `table`, nên hai loại này viết tay hoặc sửa từ nháp ở bước 1; `metadata.difficulty` và `domain` gán lúc review. DeepEval Synthesizer (lựa chọn thứ hai mà spec nêu) bị loại vì là dependency mới, trong khi `ragas` đã có trong `pyproject.toml`. Chưa kiểm tra (unverified): `TestsetGenerator` khai báo `llm: BaseRagasLLM` (`.venv/lib/python3.12/site-packages/ragas/testset/synthesizers/generate.py:70`), khác kiểu `llm_factory` đang dùng trong `retrieval_eval`; nó có chạy với model free tier qua OpenRouter, sinh câu tiếng Việt, và xử lý tài liệu ngắn hay không. Nếu không chạy được, bước 1 và 2 làm bằng tay, phần còn lại giữ nguyên.

## Khớp evidence với chunk

Một chunk khớp một evidence khi cả ba điều kiện đúng:

- `source` của chunk bằng `doc_id`;
- `page` của evidence là `null`, hoặc bằng locator của chunk (`page`, `sheet` hoặc `slide`);
- span khớp text của chunk theo quy tắc dưới đây.

Chuẩn hoá cả span và text chunk: Unicode NFC, chữ thường, gộp mọi khoảng trắng liên tiếp thành một dấu cách. Quy tắc khớp:

1. Span đã chuẩn hoá là chuỗi con của text chunk đã chuẩn hoá: khớp.
2. Ngược lại, tính tỉ lệ phủ token: số token của span có trong chunk (đếm theo multiset) chia cho số token của span. Token là âm tiết tách theo khoảng trắng sau khi bỏ dấu câu, không dùng tokenizer hay stemmer tiếng Anh. Khớp khi tỉ lệ >= `evidence_overlap_min`, giá trị (pending), chọn bằng cách xem các trường hợp span bị cắt ngang ranh giới chunk trong `retrieval_samples.jsonl`.

Quy tắc 2 xử lý trường hợp span nằm vắt qua hai chunk liền kề. Chunk không bao giờ vượt ranh giới trang, sheet hoặc slide (thiết kế loader), nên mỗi chunk có đúng một locator. Hàm khớp nằm trong một module mới `evaluation/evidence.py`, dùng chung cho `retrieval_eval` và `ingestion_eval`.

## Hai lớp đánh giá

| Lớp | Câu hỏi | Chạy ở đâu | Cần LLM |
| --- | --- | --- | --- |
| Lớp 1, retrieval | lấy về đúng và đủ đoạn evidence không, xếp có cao không | ngay sau `retrieve`, sau rerank nếu sau này có | không |
| Lớp 2, generation | với context cho trước, câu trả lời có đúng, đủ, bám context, trích dẫn đúng không; có từ chối khi thiếu thông tin không | sau `generate_answer` | có (judge) |

`retrieval_eval` có hai mode, mặc định chạy cả hai và báo cạnh nhau:

- `e2e`: pipeline đầy đủ, retrieve rồi generate. Tính cả lớp 1 và lớp 2.
- `gold`: bỏ qua retrieval, đưa thẳng các span trong `gold_evidence` làm context cho `generate_answer` (mỗi span thành một `RetrievedChunk` với `source = doc_id`, locator theo `page`, distance 0, không áp ngưỡng). Chỉ tính lớp 2. Với câu `refuse` (không có gold evidence), context là `top_k` chunk retrieve được nhưng không áp ngưỡng, để đo việc LLM tự từ chối khi context không chứa câu trả lời. Với câu `clarify`, context là các span gold nếu có, không thì như câu `refuse`.

Chênh lệch giữa `gold` và `e2e` trên cùng metric lớp 2 cho biết lỗi nằm ở retrieval hay ở generation. Chi phí: mode `gold` gần như nhân đôi số lời gọi chat và judge mỗi câu. Với free tier và chạy tuần tự (`evaluation/retrieval_eval.py:79`), thời gian chạy và quota cũng gần gấp đôi. Thêm cờ `--mode e2e|gold|both` để chạy riêng một mode khi cần tiết kiệm quota. Quota là giới hạn request ngày của model free tier (người dùng chốt, xem retrieval doc, mục "Rate limit và hết quota"): gặp lỗi quota (exit code 7) hoặc rate limit sau khi SDK đã retry (exit code 6), `retrieval_eval` dừng cả lần chạy, giữ các dòng đã ghi trong `retrieval_samples.jsonl` (`evaluation/retrieval_eval.py:117-119`) và không ghi summary.

## Metric lớp retrieval

Tính trên câu `expected_behavior = answer`, mode `e2e`, tự động, không gọi LLM. Với `k = top_k`:

| Metric | Định nghĩa |
| --- | --- |
| `recall@k` | số gold evidence được ít nhất một chunk trong top k khớp, chia cho số gold evidence; trung bình theo câu |
| `precision@k` | số chunk trong top k khớp ít nhất một gold evidence, chia cho k; trung bình theo câu |
| `mrr` | 1/rank của chunk khớp đầu tiên, 0 nếu không có; trung bình theo câu |
| `all_evidence_hit@k` | 1 nếu mọi gold evidence đều được khớp trong top k, ngược lại 0; báo cho nhóm `multi_hop` và `comparison` |

Mẫu số của `precision@k` là k cả khi Chroma hoặc ngưỡng trả về ít hơn k chunk, theo spec. Câu bị ngưỡng từ chối nhầm có cả 4 metric bằng 0. Rerank không có trong pipeline; nếu sau này thêm (vẫn là nâng cấp có điều kiện), 4 metric được tính hai lần, trước và sau rerank.

Metric cũ:

| Metric cũ | Xử lý | Lý do |
| --- | --- | --- |
| `hit_rate@k` theo file (`evaluation/retrieval_eval.py:34-42`, `evaluation/retrieval_eval.py:50`) | bỏ | `recall@k` theo evidence chặt hơn và thay được |
| `mrr` theo file (`evaluation/retrieval_eval.py:51`) | giữ tên, đổi định nghĩa sang evidence | cùng ý nghĩa, đơn vị khớp chính xác hơn |
| `source_recall@k` (chốt ở vòng trước, chưa implement) | thay bằng `recall@k` | cùng định nghĩa ở mức evidence |
| `all_sources_hit@k` (chốt ở vòng trước, chưa implement) | thay bằng `all_evidence_hit@k` | như trên |
| RAGAS `context_precision`, `context_recall` (`evaluation/retrieval_eval.py:100-113`) | bỏ | trùng vai trò với `precision@k`/`recall@k` nhưng cần judge; bỏ để giảm quota |

## Metric lớp generation

Tính ở cả hai mode. Các metric có judge dùng `JUDGE_MODEL`.

| Metric | Áp dụng cho | Cách tính | Cần GT |
| --- | --- | --- | --- |
| `answer_accuracy` (metric mục tiêu) | `answer` và `refuse` | xem bên dưới | có |
| `faithfulness` | `answer` không bị từ chối | RAGAS `Faithfulness`: số claim được context hỗ trợ chia tổng claim (`evaluation/retrieval_eval.py:69`) | không |
| `answer_relevancy` | `answer` không bị từ chối | judge chấm theo thang điểm mức độ câu trả lời đúng trọng tâm `question` (`docs/benchmark.md:78`), prompt trong `evaluation/judge.py`; thang 1-5 quy về 0-1 là đề xuất | không |
| `citation_precision` | `answer` có ít nhất một trích dẫn | số `(doc_id, page)` được trích có trong gold evidence, chia số `(doc_id, page)` được trích; `page` của gold là `null` thì chỉ so `doc_id` | có |
| `citation_recall` | `answer` | số `(doc_id, page)` trong gold evidence được trích, chia số `(doc_id, page)` trong gold evidence | có |
| `refusal_accuracy` | `refuse` | tỉ lệ câu bị từ chối | có |
| `false_refusal_rate` | `answer` | tỉ lệ câu bị từ chối | có |
| `clarify_accuracy` | `clarify` | tỉ lệ câu mà pipeline trả `kind = clarify` và judge xác nhận câu hỏi lại nhắm đúng ít nhất hai cách hiểu trong `key_facts` | có |
| `false_clarify_rate` | `answer` | tỉ lệ câu mà pipeline trả `kind = clarify`, đo việc model hỏi lại quá mức | có |

Một câu trả lời là "từ chối" khi `kind = refuse` (do ngưỡng, quy tắc, không cần judge), hoặc khi judge xác nhận model tự từ chối bằng lời khác (cần cho mode `gold`, nơi không có ngưỡng). `kind` lấy từ `Answer` mà retrieval pipeline trả về.

`answer_accuracy` tính cho từng câu rồi lấy trung bình:

| Trường hợp | Điểm |
| --- | --- |
| `answer`, bị từ chối hoặc hỏi lại | 0 |
| `answer`, có câu trả lời | key-fact recall: judge xét từng phần tử của `key_facts` có được câu trả lời nêu đúng không, điểm là số fact có chia tổng số fact; nếu judge thấy câu trả lời mâu thuẫn với `reference_answer` thì điểm là 0 |
| `refuse` | 1 nếu bị từ chối, ngược lại 0 |

Câu `clarify` không tính vào `answer_accuracy` mà báo riêng qua `clarify_accuracy`. Cơ chế clarify đã được chốt xây trong retrieval pipeline (xem retrieval doc, mục "Hỏi lại khi câu hỏi mơ hồ"); khi nó đã implement và có baseline, có thể xét đưa câu `clarify` vào `answer_accuracy`.

RAGAS `AnswerRelevancy` đang dùng (`evaluation/retrieval_eval.py:70`) không khớp spec: nó sinh câu hỏi từ câu trả lời rồi so cosine similarity với câu hỏi gốc (`.venv/lib/python3.12/site-packages/ragas/metrics/collections/answer_relevancy/metric.py:25-26`), không phải judge chấm theo thang điểm. Thay bằng prompt judge trong `evaluation/judge.py`; điểm mới không so được với `answer_relevancy` cũ.

Judge key-fact là một prompt riêng trong module mới `evaluation/judge.py`, một lời gọi cho mỗi câu, trả JSON gồm kết quả từng fact và cờ mâu thuẫn. Chọn prompt riêng thay cho RAGAS `FactualCorrectness` (chốt ở vòng trước): spec chấm theo từng `key_fact` do người gán, còn `FactualCorrectness` tự tách claim từ text nên điểm không gắn với danh sách fact và không hiệu chỉnh được theo từng fact.

## Judge và hiệu chỉnh

- Thêm setting `JUDGE_MODEL` đọc từ env qua `core.py`, dùng cho RAGAS (thay `settings.chat_model` ở `evaluation/retrieval_eval.py:67`), cho `evaluation/judge.py` và cho bước sinh nháp golden set. `retrieval_eval` dừng khi `JUDGE_MODEL` rỗng hoặc bằng `CHAT_MODEL`. Model cụ thể chưa chọn (câu hỏi mở); cần một model free tier khác `CHAT_MODEL`. Khi bỏ RAGAS `AnswerRelevancy`, judge không còn cần embedding (`evaluation/retrieval_eval.py:68` bỏ được).
- `evaluation/judge.py` có hằng số `JUDGE_PROMPT_VERSION`; mọi thay đổi prompt judge phải tăng giá trị này. `retrieval_summary.json` ghi `judge_model` và `judge_prompt_version`; hai lần chạy khác một trong hai giá trị không so sánh được trực tiếp.
- Hiệu chỉnh (đã chốt: người dùng sẽ gán nhãn tay): người dùng gán nhãn tay 50-100 mẫu (câu hỏi, câu trả lời) cho các quyết định của judge: từng key fact có hay không, có mâu thuẫn không, có từ chối không, có hỏi lại không, điểm answer relevance. Với golden set khoảng 60 câu, 50-100 mẫu gần bằng toàn bộ golden set ở một lần chạy; có thể lấy thêm mẫu từ mode `gold` để đủ 100. Nhãn lưu ở `evaluation/datasets/judge_labels.jsonl`, script `evaluation/judge_calibration.py` tính Cohen's kappa và % đồng thuận trên từng loại quyết định, không gọi API ngoài judge, tự tính kappa không thêm dependency.
- Chỉ tin metric có judge khi kappa >= 0.7 (ngưỡng ví dụ trong spec, dùng làm mặc định). Dưới ngưỡng thì sửa prompt hoặc đổi `JUDGE_MODEL` rồi hiệu chỉnh lại; trong lúc đó `answer_accuracy` chỉ được báo kèm cảnh báo, không dùng để giữ hay bỏ thay đổi.

## Metric tuỳ chọn và vận hành

| Metric | Trạng thái | Ghi chú |
| --- | --- | --- |
| Latency retrieval, generation, end-to-end (ms) | luôn báo | đo quanh `retrieve` và `generate_answer`; ghi từng câu và p50, p95 trong summary |
| Token và chi phí mỗi câu | luôn báo | `usage.prompt_tokens`, `usage.completion_tokens` từ response chat; OpenRouter free tier có trả `usage` hay không là unverified, thiếu thì ghi `null`; chi phí free tier là 0 nhưng vẫn ghi token để so khi đổi model |
| Robustness: câu không dấu | tuỳ chọn | biến thể bỏ dấu tiếng Việt của câu `answer` (NFD, bỏ dấu tổ hợp, `đ` thành `d`), so `recall@k` và `answer_accuracy` với bản có dấu |
| Robustness: chunk nhiễu, câu sai chính tả | tuỳ chọn | chèn chunk không liên quan vào context ở mode `gold`; câu sai chính tả viết tay |
| Consistency | tuỳ chọn | chạy lại cùng câu hoặc câu diễn đạt lại, so `answer_accuracy` |
| Access violation rate | N/A | corpus chưa có phân quyền; bắt buộc khi có, cần `user_context` và mức truy cập của tài liệu |

Robustness và consistency tốn thêm quota tỉ lệ với số biến thể, nên tắt mặc định.

## Báo cáo

`retrieval_samples.jsonl`, mỗi dòng một cặp (câu, mode): `qid`, `mode`, `metadata`, `expected_behavior`, `answer`, `refused`, `retrieved` (doc_id, locator, distance của từng chunk trước khi lọc), `matched_evidence`, `citations`, `invalid_citations`, điểm từng metric, kết quả judge từng key fact, `latency_ms` theo giai đoạn, `usage`.

`retrieval_summary.json`: `corpus_hash`, `golden_hash`, cấu hình chunk và `embed_model` đọc từ store, `chat_model`, `judge_model`, `judge_prompt_version`, `top_k`, `max_distance`, `evidence_overlap_min`; với mỗi mode: mọi metric trên toàn bộ và tách theo `metadata.type`, `metadata.difficulty`, định dạng file của `doc_id`; latency p50/p95, tổng token.

## Chọn ngưỡng `max_distance`

Cơ chế ngưỡng thuộc retrieval doc; giá trị được chọn bằng eval theo quy tắc eval-driven:

1. Có golden set theo schema mới, gồm câu `refuse`, chạy `retrieval_eval` khi chưa đặt `max_distance`. Đây là baseline.
2. Từ `retrieved` (distance của từng chunk trước khi lọc) trong `retrieval_samples.jsonl`, xem phân bố distance của chunk top-1 ở câu `answer` và câu `refuse`, chọn một giá trị ứng viên.
3. Đặt `max_distance` bằng giá trị đó, không đổi gì khác, chạy lại `retrieval_eval`.
4. Giữ ngưỡng khi `answer_accuracy` tăng và các metric bảo vệ, nhất là `recall@k` và `false_refusal_rate`, không xấu đi. Báo số từ `evaluation/reports/retrieval_summary.json`.
5. Ngưỡng gắn với embed model: đổi `EMBED_MODEL` là phải chọn lại ngưỡng.

## Kiểm tra gold evidence trong ingestion_eval

Thay `golden_sources_not_in_store` (`evaluation/ingestion_eval.py:48`, nguồn dữ liệu ở `evaluation/ingestion_eval.py:68`) bằng hai mục, cả hai làm fail:

- `gold_doc_ids_not_in_store`: `doc_id` trong gold evidence không có chunk nào trong store.
- `gold_evidence_not_matchable`: gold evidence (theo `qid`) không khớp được chunk nào trong store, theo quy tắc khớp trong `evaluation/evidence.py` với `evidence_overlap_min`.

Mục thứ hai bắt các lỗi sau trước khi tốn quota chạy `retrieval_eval`: span gõ sai, span lệch chuẩn hoá, locator sai, hoặc evidence bị cắt ngang ranh giới chunk sau khi đổi `chunk_size`. Ingestion cung cấp metadata để khớp (`source`, `page`/`sheet`/`slide`, text NFC); xem ingestion doc, mục "Phần ingestion phục vụ đánh giá".

## Metric mục tiêu và metric bảo vệ

- Metric mục tiêu: `answer_accuracy`, mode `e2e`.
- Metric bảo vệ, không được xấu đi khi giữ một thay đổi: `recall@k`, `precision@k`, `mrr`, `all_evidence_hit@k`, `refusal_accuracy`, `false_refusal_rate` và `false_clarify_rate` (không được tăng), `faithfulness`, `citation_precision`, `citation_recall`, `answer_relevancy`, và `answer_accuracy` ở mode `gold`.
- `clarify_accuracy`, latency, token chỉ để quan sát cho tới khi người dùng đặt yêu cầu.

Giá trị target số của mọi metric là (pending) tới khi có baseline trên corpus thật với golden set đã mở rộng.

## Baseline

`evaluation/reports/retrieval_summary.json` chưa có trong repo và store chưa được build (`evaluation/reports/ingestion.json`: `"stored_chunks": 0`).

| Metric | `e2e` | `gold` |
| --- | --- | --- |
| `answer_accuracy` (mục tiêu) | (pending) | (pending) |
| `recall@4` | (pending) | - |
| `precision@4` | (pending) | - |
| `mrr` | (pending) | - |
| `all_evidence_hit@4` | (pending) | - |
| `faithfulness` | (pending) | (pending) |
| `answer_relevancy` | (pending) | (pending) |
| `citation_precision` | (pending) | (pending) |
| `citation_recall` | (pending) | (pending) |
| `refusal_accuracy` | (pending) | (pending) |
| `false_refusal_rate` | (pending) | (pending) |
| `false_clarify_rate` | (pending) | (pending) |
| `clarify_accuracy` | (pending) | (pending) |
| latency p95 end-to-end (ms) | (pending) | (pending) |
| token mỗi câu | (pending) | (pending) |
| kappa của judge | (pending) | (pending) |

Thứ tự để có baseline: implement ingestion và retrieval pipeline theo hai doc pipeline; chạy ingestion và `ingestion_eval` đến khi pass; xây golden set theo quy trình trên; hiệu chỉnh judge; chạy `retrieval_eval`.

## Thay đổi code cần làm

| File | Thay đổi |
| --- | --- |
| `core.py:55-127` | thêm `judge_model` (`JUDGE_MODEL`), `evidence_overlap_min` (trong `config.yaml`); `max_distance` thuộc thay đổi pipeline |
| retrieval pipeline | các thay đổi pipeline (trích dẫn, `Answer`, `locator`, ngưỡng, clarify, `usage`) nằm ở retrieval doc, mục "Thay đổi code cần làm"; eval chỉ dùng kết quả |
| `evaluation/golden.py:7-31` | `GoldenSample` theo schema mới, kiểm tra như mục "Schema ground truth" |
| `evaluation/datasets/golden.jsonl` | chuyển 6 câu sang schema mới, rồi mở rộng tới khoảng 60 câu |
| `evaluation/evidence.py` (mới) | chuẩn hoá text và khớp evidence với chunk |
| `evaluation/judge.py` (mới) | judge key-fact, answer relevance theo thang điểm, từ chối, hỏi lại; `JUDGE_PROMPT_VERSION` |
| `evaluation/retrieval_eval.py:26-28` | danh sách metric mới, bỏ `context_precision`, `context_recall`, `AnswerRelevancy` của RAGAS |
| `evaluation/retrieval_eval.py:34-42` | thay `first_hit_rank` theo file bằng metric lớp retrieval theo evidence |
| `evaluation/retrieval_eval.py:46-55` | `summarize` theo mode và theo nhóm `metadata`, tránh chia cho 0 khi nhóm rỗng |
| `evaluation/retrieval_eval.py:59-72` | judge dùng `JUDGE_MODEL`, dừng khi bằng `CHAT_MODEL`; đọc `corpus_hash` và cấu hình từ store, so với hash tính lại |
| `evaluation/retrieval_eval.py:80-115` | vòng lặp theo mode `e2e`/`gold`, đo latency, ghi `usage`, các trường mới của từng dòng |
| `evaluation/retrieval_eval.py:121-127` | summary thêm phiên bản corpus, judge, cấu hình; cờ `--mode` |
| `evaluation/judge_calibration.py` (mới), `evaluation/datasets/judge_labels.jsonl` (mới) | hiệu chỉnh judge |
| `evaluation/generate_golden.py` (mới) | sinh nháp `golden_draft.jsonl` bằng RAGAS `TestsetGenerator` |
| `evaluation/ingestion_eval.py:48`, `evaluation/ingestion_eval.py:68` | thay `golden_sources_not_in_store` bằng kiểm tra gold evidence (mục "Kiểm tra gold evidence trong ingestion_eval"); kiểm tra `corpus_hash` thuộc ingestion doc |
| `README.md` | mục "Golden set format" và "Retrieval metrics" |

## Test cần thêm

Mọi test không gọi mạng; judge và chat model được thay bằng hàm giả giống `fake_embed`.

- `load_golden` từ chối `qid` trùng, `expected_behavior` lạ, câu `answer` thiếu evidence hoặc key fact, câu `refuse` có evidence, `doc_id` không tồn tại; chấp nhận `doc_id` trong thư mục con.
- Khớp evidence: span là chuỗi con sau chuẩn hoá NFC và khoảng trắng; span vắt hai chunk khớp theo tỉ lệ token; khác locator thì không khớp; `page: null` bỏ qua locator.
- `recall@k`, `precision@k`, `mrr`, `all_evidence_hit@k` trên các ví dụ nhỏ, kể cả danh sách lấy về rỗng và ít hơn k chunk.
- `citation_precision`, `citation_recall` với `page: null` và có `page`.
- `answer_accuracy`: câu `refuse` bị từ chối được 1; câu `answer` bị từ chối được 0 mà không gọi judge; cờ mâu thuẫn cho 0; 2 trên 3 fact cho 0.667.
- Mode `gold` dựng context từ span và không gọi `retrieve` với câu `answer`.
- `retrieval_eval` dừng khi `JUDGE_MODEL` rỗng hoặc bằng `CHAT_MODEL`, và khi `corpus_hash` của store lệch `data/docs`.
- Kappa của `judge_calibration` trên ví dụ tính tay.
- `ingestion_eval` fail với `gold_evidence_not_matchable` khi span không có trong docs, và pass khi span có.
- Test của pipeline (trích dẫn, ngưỡng, clarify) nằm ở retrieval doc.

## Quy trình thay đổi

Theo `CLAUDE.md`, mọi thay đổi chunking, embed model, `top_k`, prompt, ngưỡng hay cách retrieve đi theo các bước:

1. Chạy `retrieval_eval` trên cấu hình hiện tại, lưu `retrieval_summary.json` làm mốc. Kiểm tra `corpus_hash`, `golden_hash`, `judge_model`, `judge_prompt_version` của mốc.
2. Đổi đúng một biến. Nếu biến đó ảnh hưởng tới store (`EMBED_MODEL`, `chunk_size`, `chunk_overlap`, loader), chạy lại ingestion và `ingestion_eval`.
3. Chạy lại `retrieval_eval`; chỉ so khi `corpus_hash`, `golden_hash`, `judge_model`, `judge_prompt_version` giống mốc.
4. Chỉ giữ thay đổi khi `answer_accuracy` (`e2e`) tăng và các metric bảo vệ không xấu đi. Nếu chênh lệch nhỏ, thêm câu hỏi vào golden set trước khi kết luận.
5. Khi `recall@k` thấp, đọc `retrieval_samples.jsonl` để xem evidence có bị cắt ngang ranh giới chunk không, trước khi đổi embed model. Khi `answer_accuracy` ở `gold` cũng thấp, lỗi nằm ở generation, không phải retrieval.

## Câu hỏi đã trả lời

- Metric mục tiêu: độ chính xác của câu trả lời cuối cùng, `answer_accuracy` theo key-fact recall cộng kiểm tra mâu thuẫn; giá trị target (pending).
- Metric phủ đủ evidence: có, `all_evidence_hit@k` cạnh `recall@k`.
- Judge trùng `CHAT_MODEL`: không, dùng `JUDGE_MODEL` riêng.
- Hiệu chỉnh judge: người dùng sẽ gán nhãn tay 50-100 mẫu.
- Bước 3 và 4 của quy trình xây ground truth: lấy từ `docs/benchmark.md:49-50` (LLM tách key facts; bổ sung câu `refuse` và `clarify` bằng tay hoặc LLM sinh rồi kiểm tra).
- Golden set trỏ tới thư mục con: được, `doc_id` là path tương đối.

## Câu hỏi mở

- `JUDGE_MODEL` dùng model nào? Cần một model free tier khác `CHAT_MODEL` trên OpenRouter.
- Tỉ lệ single-hop và multi-hop khi sinh nháp golden set là bao nhiêu?
- Ngưỡng kappa 0.7 (ví dụ trong spec) có giữ làm mặc định không?
- Với giới hạn request ngày của free tier, golden set khoảng 60 câu chạy cả hai mode có vừa trong một ngày không? Cần đếm số lời gọi chat và judge mỗi câu khi implement; quyết định mode `gold` có chạy mặc định không.
