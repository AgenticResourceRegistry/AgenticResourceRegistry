<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Kiro enveloped transcript fixture

Sanitized excerpt of a local Kiro session in the V3/IDE messages.jsonl layout. The local CLI used for these checks is 2.35.0. Native envelope, payload types, timestamps, operation types, model fields and tool/result links are retained. Record IDs, user/assistant/tool text and tool arguments are replaced; signatures and unrelated session state are omitted. The companion file contains only the model identifier. Failure and explicit-model variations in tests are constructed cases, not claims of a live run on those models.
