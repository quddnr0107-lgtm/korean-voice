# One approval for the archived MS-147 owner export

This future-use job is independent of the earlier running matrix export. It
never automatically runs on addition or changes to the workflow or this guide.

To request a new export after explicit owner authorization, add or update
`bora-production/ms147-single-approval-request.json` on the authorized
`integrated-lectures-premium-20261028` branch. Use this exact schema with a new
GitHub request nonce, 16 to 64 lowercase hexadecimal characters:

```json
{"schema":"ms147-single-owner-export/v1","nonce":"0123456789abcdef"}
```

Do not commit an activation file before authorization. Check that no previous
run is still handling this inventory. On pull requests the workflow runs only
scope and synthetic tests and cannot access a media key. On an authorized push
containing the request, one job requests the unchanged protected environment.
Designated GitHub reviewers handle the one approval for the whole job.

The job downloads existing immutable ciphertext while the media key is absent,
seals originals offline to the unchanged owner recipient, and uploads encrypted
packets as 10 distinct artifacts (maximum 16 lessons each; 147 total).
The first nine files each pair two 8-lesson shards, and the final file has 3 lessons.
Artifact contents retain shard folders when paired; validate by lesson IDs. The
exact archived release, asset digest and lesson identity checks are shared with
the existing exporter. No public release, media redistribution, synthesis or
website deployment happens. Verify artifacts before reporting. If a later
request is for another inventory, use independently reviewed pins and keep
the same security controls; this workflow is intentionally not general-purpose.
