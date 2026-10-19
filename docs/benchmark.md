# Đặc tả Benchmark Data: RAG

> Mục tiêu: định nghĩa **data**, **ground truth**, **các tầng đánh giá** và **metrics** cho từng cấu phần, để kết quả benchmark vừa cho biết hệ thống tốt đến đâu, vừa chỉ ra lỗi nằm ở tầng nào.

---

## RAG

### 1 Data

- Corpus: các chunk (hoặc tài liệu gốc), đóng băng ở một phiên bản cố định: nguồn để retrieve ở tầng Retrieval, đồng thời là căn cứ chấm Faithfulness ở tầng Generation
- Bộ câu hỏi (factoid, multi-hop, so sánh, hỏi bảng, câu không có trong tài liệu, câu mơ hồ): input cho cả 2 tầng Retrieval và Generation
- Thông tin quyền người hỏi và mức truy cập của tài liệu: đánh giá Access violation rate, khi corpus có phân quyền

### 2 Ground truth đề xuất

**JSON mẫu (1 câu hỏi)**

```json
{
  "qid": "q_001",
  "question": "...",
  "user_context": {"role": "employee", "permissions": ["hr_public"]},
  "metadata": {"type": "multi_hop", "difficulty": "medium", "domain": "HR"},
  "expected_behavior": "answer",
  "gold_evidence": [
    {"doc_id": "doc_A", "page": 2, "span": "..."},
    {"doc_id": "doc_A", "page": 3, "span": "..."}
  ],
  "reference_answer": "...",
  "key_facts": ["ý 1", "ý 2"]
}
```

| Trường | Dùng cho metric |
|---|---|
| `gold_evidence` (doc_id + trang + đoạn text) | Recall@k, Precision@k, MRR, Citation Precision / Recall |
| `reference_answer`, `key_facts` | Answer Correctness |
| `expected_behavior` (`answer` / `refuse` / `clarify`) | Refusal Accuracy |
| `user_context` | Access violation rate |
| `metadata` | Chia nhóm khi báo cáo |

> Gán evidence ở mức **tài liệu + đoạn văn**, không theo chunk ID, vì đổi chunking thì chunk ID thay đổi. Với câu hỏi không có trong tài liệu: `"expected_behavior": "refuse"`, `"gold_evidence": []`.

**Cách xây dựng GT**

1. **Sinh câu hỏi từ corpus bằng framework:** Ragas (testset generation) hoặc DeepEval (Synthesizer) sinh câu hỏi, câu trả lời chuẩn và đoạn context nguồn. Cấu hình tỷ lệ câu single-hop / multi-hop theo nhu cầu.
2. **Map context nguồn về `gold_evidence`:** lấy `doc_id`, trang và đoạn text từ metadata của chunk mà framework dùng để sinh câu hỏi.
3. **Tách key facts:** dùng LLM tách `reference_answer` thành danh sách `key_facts`.
4. **Bổ sung câu khó:** tự viết, hoặc dùng LLM sinh rồi kiểm tra, các câu không có trong tài liệu (`refuse`) và câu mơ hồ (`clarify`). Framework thường không sinh hai loại này.
5. **Người review:** kiểm tra câu hỏi tự nhiên, câu trả lời đúng với tài liệu; loại câu quá dễ (chép nguyên văn từ tài liệu). Dùng model khác với model của hệ thống RAG đang được đánh giá.

### 3 Các tầng đánh giá

| Tầng | Câu hỏi cần trả lời | Cách cô lập |
|---|---|---|
| **1. Retrieval** | Có lấy được đúng và đủ các đoạn chứa thông tin cần thiết không? Đoạn đúng có nằm ở thứ hạng cao không? | Chấm điểm ngay sau bước retrieve (và sau rerank nếu có), không cần chạy LLM |
| **2. Generation** | Với context được cung cấp, câu trả lời có đúng, đủ, bám tài liệu, có trích dẫn đúng không? Có biết từ chối khi không có thông tin không? | Đưa thẳng **gold evidence** vào LLM (bỏ qua retrieval) để đo năng lực generate thuần |

### 4 Metrics đề xuất theo tầng

#### Tầng 1: Retrieval

| Metric | Ý nghĩa | Cách đánh giá |
|---|---|---|
| **Recall@k** | Trong top-k chunk lấy ra, tìm được bao nhiêu % gold evidence | So top-k chunk với `gold_evidence`; chunk khớp khi chứa (hoặc trùng phần lớn với) `span`. Số evidence tìm được / tổng số evidence. Tính tự động |
| **Precision@k** | Trong top-k chunk, bao nhiêu % là chunk liên quan | Số chunk khớp `gold_evidence` / k. Tính tự động |
| **MRR (Mean Reciprocal Rank)** | Chunk đúng đầu tiên nằm ở vị trí thứ mấy | Trung bình 1/rank của chunk đầu tiên khớp `gold_evidence`. Tính tự động |

> Đo **trước và sau rerank** để biết rerank có thật sự cải thiện không.

#### Tầng 2: Generation

| Metric | Ý nghĩa | Cách đánh giá |
|---|---|---|
| **Answer Correctness** (Key-fact recall) | Câu trả lời có đủ các ý đúng và không có ý sai | **LLM-as-judge**: kiểm tra từng ý trong `key_facts` có xuất hiện không, và câu trả lời có mâu thuẫn với `reference_answer` không |
| **Faithfulness / Groundedness** | Mọi khẳng định trong câu trả lời có được tài liệu đã retrieve hỗ trợ không | **LLM-as-judge**: tách câu trả lời thành các claim, kiểm tra từng claim với các chunk đã retrieve. Không dùng GT. = số claim được hỗ trợ / tổng số claim |
| **Answer Relevance** | Câu trả lời có đúng trọng tâm câu hỏi không, hay lan man/né tránh | **LLM-as-judge** chấm theo thang điểm, so với `question`. Không cần GT |
| **Citation Precision / Recall** | Trích dẫn có đúng nguồn, có đủ nguồn không | So các nguồn được trích (doc_id, trang) với `gold_evidence`. Precision: trích đúng / tổng trích dẫn. Recall: nguồn gold được trích / tổng nguồn gold. Tính tự động |
| **Refusal Accuracy** (và False Refusal Rate) | Từ chối đúng khi tài liệu không có thông tin; không từ chối khi tài liệu có | Dựa vào `expected_behavior`. Nhận diện câu trả lời có phải từ chối không bằng rule hoặc **LLM-as-judge**. Trên tập `refuse`: % từ chối đúng. Trên tập `answer`: % từ chối nhầm |

> **Hiệu chỉnh LLM-as-judge**: lấy 50–100 mẫu cho người chấm, tính độ đồng thuận giữa judge và người (Cohen's kappa hoặc % agreement). Chỉ tin điểm judge khi độ đồng thuận đủ cao (ví dụ kappa ≥ 0.7). Ghi lại version prompt và model của judge.

### 5 Các metrics khác có thể dùng thêm

| Metric | Ý nghĩa (khía cạnh khác) | Khi nào nên dùng |
|---|---|---|
| **Noise Sensitivity / Robustness** | Kết quả thay đổi thế nào khi thêm chunk nhiễu hoặc câu hỏi viết sai chính tả, không dấu | Đánh giá độ bền khi dùng thực tế |
| **Consistency** | Hỏi cùng một câu nhiều lần (hoặc diễn đạt khác) có ra cùng kết quả không | Hệ thống phục vụ nghiệp vụ cần câu trả lời ổn định |
| **Access violation rate** | % trường hợp trả về nội dung user không có quyền xem, dựa vào `user_context` và mức truy cập của tài liệu | Bắt buộc nếu hệ thống có phân quyền tài liệu |
| **Latency (retrieval / generation / end-to-end), Token & Cost per query** | Thời gian và chi phí cho mỗi câu hỏi | Luôn nên báo cáo kèm |
