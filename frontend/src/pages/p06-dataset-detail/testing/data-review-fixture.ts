import type { DataReview, DataReviewPage } from "../../../features/datasets/episodes/data-reviews";
import { deliveryFixture } from "../../p08-data-annotation/testing/delivery-fixture";

export function dataReviewFixture() {
  const { scope, job } = deliveryFixture();
  const data = job.plan.binding.data;
  const review: DataReview = {
    format: "hc-episode-data-review/1",
    review_id: "er_first",
    data,
    sequence: 1,
    previous_review_id: null,
    decision: "RETURNED",
    actor_id: "capture-reviewer",
    created_at: "2026-09-29T10:00:00Z",
    comment: "需补齐任务覆盖",
    locations: [],
  };
  const page: DataReviewPage = {
    data,
    status: "RETURNED",
    current_sequence: 1,
    latest: review,
    items: [review],
    next_before: null,
    allowed_actions: [
      {
        action: "SUBMIT_DATA_REVIEW",
        allowed: true,
        required_capabilities: ["dataset_version.review"],
        denied_reason: null,
      },
    ],
  };
  return { scope, data, review, page };
}
