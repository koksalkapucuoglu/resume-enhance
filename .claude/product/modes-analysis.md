# Standard vs agentic mode — analysis (October 2026)

Read from the code on branch `presales-cleanup` (dashboards, editor, agent tools,
mode toggle) and from using both modes as a new user.

## How the two modes work today

- The mode is an **account setting** (`UserProfile.ui_mode`). New accounts get
  **agentic** (`UI_MODE_DEFAULT`). It changes from three places: the dashboard
  header toggle, the editor header toggle, and the Profile page.
- Only `/dashboard/` differs: `dashboard.html` (cards) or `dashboard_agentic.html`
  (chat + side panel). **The editor (`/form/<id>/`) is the same page in both
  modes.**
- Switching with a resume open carries it across: agentic → standard opens that
  resume in the editor; editor → agentic opens chat with that resume active.

## Does switching confuse? Yes, in five places

| # | Where | What happens | Why it confuses |
|---|---|---|---|
| 1 | Editor header | Always shows **Standard** highlighted, even for an agentic user who came from chat | The toggle claims a mode the account is not in; "Dashboard" from the editor then lands in chat |
| 2 | Agentic → "Edit" | Opens the editor in a **new tab** | Two tabs of the same app; going "back" to chat from the editor's toggle opens a third agentic view |
| 3 | Agentic → Standard (with a resume active) | Lands in the **editor**, not the card list | People expect the other dashboard, not a form |
| 4 | Notices | Agentic does not render Django messages ("verify your email", "saved", "deleted"); they wait and appear later in standard, sometimes contradicting each other | The first thing a new user (agentic by default) misses is "we sent you a link" |
| 5 | Chat history | Lives in the browser (per account, per device) | Switching device or clearing data loses the conversation; standard mode has nothing equivalent to lose, so it feels like data loss |

The deeper reason: one is a **document view**, the other a **conversation view**,
and both are labelled as "modes" of the whole account. People do not think "I am
an agentic user"; they think "I want to change this bullet" or "I want to ask".

## What exists in one mode but not the other

| Capability | Standard | Agentic | Note |
|---|---|---|---|
| Score a resume against a job posting | ❌ | ✅ (Application Score, paste detection, `evaluate_posting`) | **The paid workflow is chat-only.** Standard only shows job copies + their last score under a card |
| Improve a posting's gaps (question → draft → apply) | ❌ | ✅ | |
| Promote a job copy over the main resume | ❌ | ✅ | |
| Create a language version / translate | ❌ | ✅ (`create_translated_copy`, `translate_resume`) | Standard cannot even change a resume's language: `resume_language` is a hidden field |
| List language versions | ❌ | ✅ | Standard shows them as unrelated cards |
| Delete / duplicate / download | ✅ (cards) | ✅ (tools, delete asks approval) | |
| Undo | ✅ history modal | ✅ history modal + "undo last" + `revert_last_change` | |
| Field-by-field form editing, add/remove entries | ✅ | ❌ (opens the editor) | |
| AI rewrite of one bullet (✨ button) | ✅ | 🟡 via `modify_resume` | |
| Import flags ("not in the PDF") | ✅ editor banner | ❌ not shown in chat | Import from chat never tells the person what was flagged until they open the editor |
| Rename a resume | ✅ title field | ❌ no tool | |
| Template + "What I'm working on" | ✅ Template tab | ✅ Template pane | parity |
| Start-page choice (blank / import) | ✅ `/start/` | ✅ chips / chat | |
| Feedback widget | ✅ | ❌ | |
| Usage / limits | Profile page | "My Limits" chip (costs a credit) | |

## Cost and friction specific to agentic

- **Every quick chip is a chat message.** "List", "My Limits", "Help", "New
  Resume", "Upload PDF", the empty-state buttons, "Preview resume N" on cards and
  "Switch to X" in template cards all call `sendMessage` → one LLM turn →
  **one AI credit each**. A new user can spend a fifth of the free month on
  navigation.
- `switch_template` is marked destructive → asks for approval, although a
  template change is cosmetic and reversible.
- `modify_resume` asks for approval on every edit of a non-empty resume
  (unless the user turns confirmations off in Profile). Safe, but slow for a
  chat-first flow; undo already exists.

## Recommendation

**Short term (small, removes most confusion):**
1. Quick chips, empty-state buttons, card actions and template cards call their
   endpoints directly — no LLM, no credit (list, limits, preview, switch
   template, upload picker, new blank resume). Keep the LLM for free text.
2. Editor header: drop the mode toggle; show "← Dashboard" (and "Ask AI" that
   opens chat with this resume). The editor is not a mode.
3. Agentic "Edit" opens the editor in the same tab.
4. Render Django messages as bot bubbles in agentic.
5. `switch_template` not destructive.
6. Show import flags as a chat card after an import in chat.

**Next (parity of the paid workflow):**
7. "Score against a posting" in the editor's right pane, reusing
   `evaluation_service.panel` and the improve cards.
8. Language version: "Translate / add TR version" on the card and in the editor.

**Direction (one workspace instead of two modes):** one screen per resume —
form + preview, with the assistant as a side panel ("Ask AI") that knows the
open resume; the dashboard is just the list. The chat becomes a tool inside the
document, not a different product. Agentic's strengths (approvals, undo,
posting flow) all carry over; the account setting and the mode toggle disappear.
This is the larger change; the short-term list is a step towards it and is
worth doing either way.
