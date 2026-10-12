"""
helpbot.py - the "Help" guide for new users: what each page does and where to find things.

It answers from the fixed help topics below (and only those). It has no tools and never sees invoice, vendor
or payment data, so it can't look up, approve, reject or pay anything. Topics are found with plain keyword
matching; when the AI is on it only rephrases the matching topics into a short answer.
"""
import re

import tools

# id, title, keywords, answer, (link text, endpoint), roles that see it (None = everyone)
TOPICS = [
    ("dashboard", "The dashboard", "dashboard home start overview main page numbers cards",
     "The Dashboard is the home page. The four cards at the top show invoices that need AP review, invoices "
     "waiting for a manager, invoices ready for payment and the money saved. Below them: 'Needs your attention', "
     "the latest manager decisions, the invoice list with search and filters, and the live agent activity.",
     ("Open the dashboard", "dashboard"), None),
    ("flagged", "Where to see flagged invoices", "flagged flag problem review hold attention needs ap review red",
     "Flagged invoices have the status 'AP review required'. Click the 'Needs AP review' card on the dashboard, or "
     "the 'AP review required' chip above the invoice list. Open one to see 'Where it went wrong': each problem in "
     "plain words, the invoice vs PO table and the original file with the wrong values boxed in red.",
     ("Show flagged invoices", "dashboard?status=Flagged"), None),
    ("statuses", "What the statuses mean", "status statuses mean meaning approved pending flagged rejected processing "
     "badge label",
     "Processing: the agent is still checking it. Approved: every code check passed (or a manager approved it); the "
     "ledger entry is 'Ready for payment'. Pending approval: the checks passed but a manager must approve (over the "
     "limit, a small difference, or an AI item match to confirm). AP review required (Flagged): a problem was "
     "found and the AP team must look. Rejected: a duplicate, or a person rejected it.", None, None),
    ("payment_states", "Ready for payment, paid, cancelled", "ready payment paid ledger cancelled awaiting record "
     "payment mark paid",
     "ClearPay never pays anyone. 'Ready for payment' means a ledger entry is prepared. After the money is paid "
     "outside ClearPay, an AP user presses 'Record payment' on the invoice so it leaves the deadline list. "
     "'Awaiting approval' means a manager still has to decide; 'Cancelled' means it was rejected.", None, None),
    ("upload", "Uploading invoices", "upload add new invoice file pdf drag drop scan",
     "Go to 'Upload invoice' and drag in PDF, TXT, PNG or JPG files (up to 10 at once, 10 MB each). You can watch "
     "each check happen live. Invoices can also arrive by themselves from the invoices email inbox or the "
     "invoices_inbox folder.", ("Upload an invoice", "upload"), ("ap", "admin")),
    ("email_inbox", "Invoices arriving by email", "email inbox mailbox imap automatic pickup arrive gmail",
     "If an admin has connected the invoices mailbox, ClearPay reads new emails every minute, takes the invoice "
     "attachments (or an invoice typed in the email body), and checks them like an upload. They show 'arrived by "
     "email' on the invoice. Nothing is approved or paid because an email arrived.", None, None),
    ("approvals", "Approving invoices", "approve approval approvals manager reject decide decision",
     "Managers approve or reject invoices on the 'Approvals' page or on the invoice itself. You can't decide on an "
     "invoice you uploaded yourself (segregation of duties). AP users can reject a flagged invoice or 'Send to "
     "manager' after reviewing it.", ("Open Approvals", "approvals"), ("manager", "admin")),
    ("send_to_manager", "Sending a flagged invoice to a manager", "send manager escalate forward flagged ap",
     "Open the flagged invoice, check the problems and the investigation, add a comment if useful and press "
     "'Send to manager'. The managers are notified.", None, ("ap", "admin")),
    ("investigation", "The Investigator (AI investigation)", "investigation investigator investigate ai "
     "recommendation recommend why flagged agent analysis",
     "Every flagged invoice gets an investigation. It looks at the invoice, the purchase order, the vendor's history "
     "and similar past cases with what managers decided, then recommends Approve, Reject or Ask the vendor, with "
     "its evidence and a draft email to the vendor. Open the invoice and look for the 'Investigation' panel. It is "
     "only a recommendation: a person always makes the decision.", None, None),
    ("smart_match", "Smart matching (AI item matching)", "smart match matching ai matched item different wording "
     "description units boxes",
     "When an invoice describes an item differently from the PO (\"copier paper, 5 boxes\" vs \"A4 paper ream × 50\"), "
     "the AI says whether they are the same item and how the units convert. Code then checks the quantity, price "
     "and totals. Invoices that needed an AI match always go to a person to confirm; on the invoice they're marked "
     "'AI match'.", None, None),
    ("risk", "Vendor risk score", "risk score vendor trust rating high medium low",
     "Each vendor gets a risk score from 0 to 100, worked out from its past invoices: duplicates, bank-detail changes, "
     "PO mismatches, price differences, arithmetic errors and rejections. Low is under 25, Medium 25-49, High 50+. "
     "It's only a warning to look more carefully; it never blocks or approves anything. See the 'Vendors' page.",
     ("Open Vendors", "vendors"), None),
    ("deadlines", "Payment deadlines", "deadline deadlines due date overdue late penalty payment terms msme days "
     "remaining",
     "The 'Payment deadlines' page lists every unpaid invoice by due date: 'Overdue by 3 days', 'Due tomorrow', "
     "'Due in 5 days'. The due date comes from the invoice (due date or payment terms), otherwise the default terms. "
     "Small suppliers marked MSME must be paid within 45 days. ClearPay only tracks deadlines; it never pays.",
     ("Open Payment deadlines", "deadlines"), None),
    ("analytics", "Analytics", "analytics spend fraud money saved savings report business impact chart",
     "The 'Analytics' page shows approved spend (by vendor and month), fraud caught (duplicates, look-alike vendors, "
     "bank changes...) and money saved, plus how often people agreed with the Investigator.",
     ("Open Analytics", "analytics"), None),
    ("ask", "Ask ClearPay (chat)", "ask clearpay chat question chatbot assistant why",
     "'Ask ClearPay' answers questions about your data, for example “Why was BET-3002 flagged?” or “Show me the "
     "previous issues with Alpha”. It looks the answer up in ClearPay's records and says so when something isn't "
     "there. It can't approve, reject or pay.", ("Open Ask ClearPay", "ask_page"), None),
    ("plain_english", "Searching in plain English", "search plain english find filter query type sentence",
     "On the dashboard, type a sentence in 'Ask in plain English', such as “flagged invoices from Alpha over "
     "₹50,000” or “overdue invoices”. ClearPay shows how it understood you as chips above the results.",
     ("Open the dashboard", "dashboard"), None),
    ("pos", "Purchase orders", "purchase order orders po create edit add new",
     "The 'Purchase orders' page lists every PO with its lines and the invoices billed against it. Everyone can view "
     "them. Only an admin can create or edit a PO; AP users and managers can't change them.",
     ("Open Purchase orders", "pos"), None),
    ("emails", "Email drafts", "email emails draft drafts send vendor message",
     "ClearPay drafts emails (to managers, vendors or the AP team) but never sends them by itself. Open 'Email "
     "drafts', check or edit the text and press Send. Vendor emails always go to the address on file.",
     ("Open Email drafts", "emails"), None),
    ("audit", "Audit log", "audit log history trail who did what tamper",
     "The 'Audit log' records every step by the agent, the Investigator and people, with time and reason. Each row "
     "is chained to the previous one, so the page can show that nothing was altered.", ("Open the Audit log", "audit"),
     None),
    ("confidence", "Read confidence", "confidence bar read accuracy extraction sure",
     "The confidence bar shows how sure ClearPay is that it read the invoice correctly (green 0.9+, amber 0.7-0.9, "
     "red below 0.7). Below 0.7 a person must check the figures against the original file.", None, None),
    ("notifications", "Notifications", "notification notifications bell alert toast unread",
     "The bell at the top shows notifications: decisions on your invoices, invoices that need you, security alerts. "
     "New ones also pop up in the bottom-right corner.", ("All notifications", "notifications_page"), None),
    ("testing", "Quality & testing pages", "evaluation red team redteam what-if regression comparison rules vs ai "
     "testing proof quality trust",
     "'Quality & testing' (in the Admin part of the menu) has five tabs: the Evaluation report (accuracy on labelled "
     "invoices), Rules vs AI (rules alone compared with rules + AI on tricky invoices), the Red-team lab (attack "
     "invoices), What-if replay (try different rules on past invoices) and Decision regression.",
     ("Open Rules vs AI", "compare"), None),
    ("settings", "Settings and users", "settings users admin rules limit tolerance password msme",
     "Admins change the approval limit, tolerances, payment terms, MSME vendors, users and passwords in 'Settings & "
     "users'.", ("Open Settings", "settings"), ("admin",)),
    ("roles", "Who can do what", "role roles permission permissions can i allowed ap manager admin who",
     "AP team: upload, review flagged invoices, send them to a manager, reject flagged ones, record payments. "
     "Managers: approve or reject pending invoices. Admin: everything, plus purchase orders, rules and users. The AI "
     "can only investigate, explain and recommend; it can never approve, reject or pay.", None, None),
]
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "i", "do", "does", "how", "what", "where", "can", "is", "are", "to", "of", "in", "on", "my",
         "me", "see", "find", "this", "it", "for", "and", "or", "you", "show", "mean", "means", "which", "with"}

PROMPT = """You are ClearPay's Help guide for new users. Answer the question using ONLY the help topics given.
If they don't cover it, say you don't know and suggest the closest topic. Two to four short sentences, plain text.
You only explain the app: you cannot look up invoices or vendors, approve, reject or pay anything.
The user's role is: {role}."""


def _tokens(s):
    return [w for w in _WORD.findall((s or "").lower()) if w not in _STOP]


def search(question, role=None, k=3):
    q = _tokens(question)
    scored = []
    for t in TOPICS:
        if t[5] and role and role not in t[5]:
            continue
        kw = set(_tokens(t[2] + " " + t[1]))
        body = set(_tokens(t[3]))
        score = sum(3 if w in kw else 1 if w in body else 0 for w in q)
        score += sum(2 for w in q for k_ in kw if len(w) > 4 and k_.startswith(w[:5]) and w != k_)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:k]]


def answer(question, role=None, use_ai=True):
    """Returns {answer, links, mode}. Links are endpoints the page turns into URLs."""
    question = (question or "").strip()[:300]
    hits = search(question, role)
    links = [{"label": t[4][0], "endpoint": t[4][1]} for t in hits if t[4]][:2]
    if not hits:
        return {"answer": "I can only help with how ClearPay works, and I don't have a topic for that. Try “where can I "
                          "see flagged invoices?”, “what does Pending mean?” or “where are payment deadlines?”.",
                "links": [], "mode": "rules"}
    if use_ai and tools.llm_enabled():
        ctx = {"invoice_id": None, "prompt_tokens": 0, "completion_tokens": 0}
        try:
            topics = "\n\n".join(f"[{t[1]}]\n{t[3]}" for t in hits)
            resp = tools.call_groq(ctx, retries=1, messages=[
                {"role": "system", "content": PROMPT.format(role={"ap": "AP team", "manager": "manager",
                                                                  "admin": "admin"}.get(role, "user"))},
                {"role": "user", "content": f"<help_topics>\n{topics}\n</help_topics>\nQuestion: {question}"}],
                max_tokens=400)
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return {"answer": text[:900], "links": links, "mode": "ai"}
        except Exception:
            pass
    return {"answer": hits[0][3], "links": links, "mode": "rules"}
