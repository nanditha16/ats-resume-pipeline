# Choosing the variant after Stage 0/1

`batch_prep.sh --continue` reads each `variant_rank_prompt_response.txt` and needs one variant name. The original
extractor tries seven patterns (a bold filename on the Top Pick line, a `| 1 |` table row, ...). JD11's response matched
none: its rank-1 rows read `| 1 (tie) |`, the section was `## Top Pick`, and the pick was named in prose ("The Acme
variant (72)."). The batch stopped with "Cannot extract variant".

`scripts/variant_pick.py` is now the fallback, consulted only when the seven patterns find nothing:

1. Rank-1 rows of the ranking table (`1`, `1 (tie)`, `1=`, `T1`), with full filenames or short names (`Initech`).
2. If the Top Pick section names one of the tied variants, that one wins.
3. A tie with no tiebreak takes the first listed, and says so on stderr.
4. No rank-1 row: the variant the Top Pick section names first.

Tested: JD11 resolves to `Jane Doe_Resume_Acme` (tied with Globex, broken by the Top Pick section). On the 26
rank responses already in the snapshot the new parser agrees with the original on all 26, and the batch output is unchanged.

If a response ever has a genuine disagreement (the table's rank 1 differs from the section's pick, as in JD19), the
table wins, and you can still override with `echo 'Name' > prompts/JD_Analysis/JDx/.chosen_variant`.
