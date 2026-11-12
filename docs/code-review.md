# Code review loop

Tweaker implements the code-review contract Sentry sends to Seer:

- `POST /v1/code_review/review-request`
- `POST /v1/code_review/check/rerun`
- `POST /v1/code_review/pr-closed`
- `POST /v1/code_review`

## Flow

1. **Receive the request** and create a `SeerCodeReview` record.
2. **Resolve the pull request**:
   - GitHub: `GET /repos/{owner}/{repo}/pulls/{pr_id}` for head/base SHAs and metadata.
   - GitLab: `GET /projects/{project}/merge_requests/{iid}` for `diff_refs`.
3. **Start the check**:
   - GitHub: `POST /repos/{owner}/{repo}/check-runs` with `status: in_progress`.
   - GitLab: `POST /projects/{project}/statuses/{sha}` with `state: running`.
4. **Fetch the diff**:
   - GitHub: `Accept: application/vnd.github.v3.diff`.
   - GitLab: merge request `changes`.
5. **Ask the model for a review** and validate every comment against a
   changed/context line in the diff.
6. **Post inline comments**:
   - GitHub: `POST /pulls/{pr_id}/reviews` with `event: COMMENT`; falls back to a
     summary issue comment if GitHub rejects inline positions.
   - GitLab: one discussion per comment with a text position; falls back to a
     summary note if the position is rejected.
7. **Complete the check**:
   - GitHub: `PATCH /check-runs/{id}` with `success`, `neutral`, or `failure`
     and annotations for each valid comment.
   - GitLab: commit status `success` or `failed`.
8. **PR closed**: cancel the matching check run/status.

## Required permissions

GitHub token:

- `checks:write`
- `pull_requests:write`
- `contents:read`

GitLab token:

- `api`
- `read_repository`

Set `TWEAKER_GITHUB_TOKEN` or `TWEAKER_GITLAB_TOKEN`. For GitHub Enterprise and
self-hosted GitLab, set `TWEAKER_GITHUB_ENTERPRISE_URL` /
`TWEAKER_GITHUB_ENTERPRISE_TOKEN` or `TWEAKER_GITLAB_SELF_HOSTED_URL` /
`TWEAKER_GITLAB_SELF_HOSTED_TOKEN`. Base URLs may include a port or path prefix.

## Behavior

- No comments: check run concludes `success`.
- Low/medium comments: `neutral`.
- High/critical comments: `failure`.
- A rerun creates a new check run and a new review; previous comments remain on
  the PR.
- Reviews are stored and exposed at `/api/code-reviews`.

## Limitations

- The review model is our own; it does not reproduce Seer's exact heuristics.
- Inline comments are limited to 50 per review.
- GitHub Enterprise Server uses `/api/v3`; GitLab self-hosted uses the configured
  `TWEAKER_GITLAB_BASE_URL`.
- GitHub review API failures fall back to a summary comment rather than dropping
  the review.
