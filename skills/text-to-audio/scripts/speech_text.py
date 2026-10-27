"""Turn a Markdown or plain-text file into speakable text, split into chapters and paragraphs.

Standard library only, so it runs (and can be tested) without the skill venv:

    python3 speech_text.py <file.md|file.txt> [--math rules|skip] [--tables skip|read] [--code skip|read]

prints the speech text with chapter marks. text_to_audio.py imports prepare() from here.
"""
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------------------------------
# LaTeX to words (rules). Good for the inline math of papers and books; a long display equation is better read by the
# LLM mode (--math llm) or skipped.
# ---------------------------------------------------------------------------------------------------------------------
GREEK = ("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi omicron pi "
         "varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega").split()
WORDS = {
    r"\cdot": " times ", r"\times": " times ", r"\div": " divided by ", r"\pm": " plus or minus ",
    r"\mp": " minus or plus ", r"\neq": " is not equal to ", r"\ne": " is not equal to ", r"\leq": " is at most ",
    r"\le": " is at most ", r"\geq": " is at least ", r"\ge": " is at least ", r"\ll": " is much less than ",
    r"\gg": " is much greater than ", r"\approx": " is approximately ", r"\sim": " is distributed as ",
    r"\simeq": " is approximately ", r"\equiv": " is equivalent to ", r"\propto": " is proportional to ",
    r"\in": " in ", r"\notin": " not in ", r"\subset": " subset of ", r"\subseteq": " subset of ",
    r"\cup": " union ", r"\cap": " intersect ", r"\emptyset": " the empty set ", r"\forall": " for all ",
    r"\exists": " there exists ", r"\to": " to ", r"\rightarrow": " goes to ", r"\Rightarrow": " implies ",
    r"\implies": " implies ", r"\iff": " if and only if ", r"\leftarrow": " gets ", r"\mapsto": " maps to ",
    r"\infty": " infinity ", r"\partial": " partial ", r"\nabla": " gradient of ", r"\otimes": " tensor ",
    r"\oplus": " x or ", r"\dagger": " dagger ", r"\ldots": " dot dot dot ", r"\cdots": " dot dot dot ",
    r"\dots": " dot dot dot ", r"\vdots": " ", r"\ddots": " ", r"\top": " transpose ", r"\perp": " perpendicular ",
    r"\circ": " composed with ", r"\star": " star ", r"\ast": " star ", r"\wedge": " and ", r"\vee": " or ",
    r"\neg": " not ", r"\lnot": " not ", r"\land": " and ", r"\lor": " or ", r"\mid": " given ",
    r"\log": " log ", r"\ln": " natural log ", r"\exp": " exp ", r"\sin": " sine ", r"\cos": " cosine ",
    r"\tan": " tangent ", r"\max": " max ", r"\min": " min ", r"\arg": " arg ", r"\argmax": " arg max ",
    r"\argmin": " arg min ", r"\det": " determinant of ", r"\tr": " trace of ", r"\Pr": " probability of ",
    r"\lim": " the limit ", r"\sup": " sup ", r"\inf": " inf ", r"\hbar": " h bar ", r"\ell": " ell ",
    r"\prime": " prime ",
}
WRAPPERS = ("mathbf mathrm mathit mathsf mathtt boldsymbol bm text textbf textit textrm operatorname mbox "
            "mathnormal displaystyle textstyle scriptstyle").split()
# Named functions (in \mathrm, \operatorname or bare): read as words, not letter by letter.
FUNCS = {"Cov": "covariance", "cov": "covariance", "Var": "variance", "var": "variance", "Corr": "correlation",
         "corr": "correlation", "tr": "trace", "Tr": "trace", "sgn": "sign", "Pr": "probability"}
STARS = ("*", r"\ast", r"\star")
ACCENTS = {"hat": "hat", "widehat": "hat", "bar": "bar", "overline": "bar", "tilde": "tilde", "widetilde": "tilde",
           "vec": "vector", "dot": "dot", "ddot": "double dot"}
DROP = (r"\left", r"\right", r"\big", r"\Big", r"\bigg", r"\Bigg", r"\bigl", r"\bigr", r"\Bigl", r"\Bigr",
        r"\quad", r"\qquad", r"\,", r"\;", r"\:", r"\!", r"\ ", r"\limits", r"\nolimits", r"&", r"\\",
        r"\nonumber", r"\notag")


def _group(s, i):
    """The argument at s[i]: a {...} group (returned without braces) or one token. Returns (arg, next index)."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == "\\":
        m = re.match(r"\\[A-Za-z]+|\\.", s[i:])
        return m.group(0), i + len(m.group(0))
    return s[i], i + 1


def _script(arg, sup):
    a = latex_to_speech(arg).strip()
    if not sup and arg.replace(" ", "") in STARS:
        return " star "
    if not sup:
        return f" sub {a} " if a else " "
    plain = arg.replace(" ", "")
    if plain == "2":
        return " squared "
    if plain == "3":
        return " cubed "
    if plain in ("T", r"\top", r"\intercal", "\\mathsf{T}", "\\mathrm{T}", "\\top"):
        return " transpose "
    if plain == "-1":
        return " inverse "
    if plain in ("*", r"\ast", r"\star"):
        return " star "
    if plain in ("\\dagger", "†"):
        return " dagger "
    if plain in ("'", r"\prime"):
        return " prime "
    return f" to the power {a} " if a else " "


def latex_to_speech(tex):
    """A spoken form of a LaTeX expression: '\\sum_{i=1}^{n} x_i^2' -> 'the sum from i equals 1 to n of x sub i
    squared'. Unknown commands lose their backslash."""
    s = tex.strip().replace("\n", " ")
    s = re.sub(r"\\(?:begin|end)\{[a-z*]+\}(\{[^}]*\})?", " ", s)
    s = re.sub(r"\\(?:no)?limits(?![A-Za-z])", "", s)
    # The rows of an aligned or cases block (\\) are separate statements; & only aligns them.
    s = re.sub(r"\\\\(\[[^\]]*\])?", " \x00 ", s).replace("&", " ")
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)|\\(.)", s[i:])
            name = m.group(1) or m.group(2)
            cmd = "\\" + name
            i += len(m.group(0))
            if name == "nabla" and i < len(s) and s[i] == "_":
                arg, i = _group(s, i + 1)
                out.append(f" the gradient with respect to {latex_to_speech(arg)} of ")
            elif cmd in DROP or name in (",", ";", ":", "!", " "):
                out.append(" ")
            elif name == "frac" or name in ("dfrac", "tfrac"):
                num, i = _group(s, i)
                den, i = _group(s, i)
                out.append(f" {latex_to_speech(num)} over {latex_to_speech(den)} ")
            elif name == "sqrt":
                if i < len(s) and s[i] == "[":
                    j = s.index("]", i)
                    root, i = s[i + 1:j], j + 1
                    arg, i = _group(s, i)
                    out.append(f" the {latex_to_speech(root)}th root of {latex_to_speech(arg)} ")
                else:
                    arg, i = _group(s, i)
                    out.append(f" the square root of {latex_to_speech(arg)} ")
            elif name in ("sum", "prod", "int", "oint", "bigcup", "bigcap", "iint"):
                word = {"sum": "the sum", "prod": "the product", "int": "the integral", "oint": "the integral",
                        "iint": "the double integral", "bigcup": "the union", "bigcap": "the intersection"}[name]
                lo = hi = None
                while i < len(s) and s[i] in "_^ ":
                    if s[i] == " ":
                        i += 1
                        continue
                    which = s[i]
                    arg, i = _group(s, i + 1)
                    if which == "_":
                        lo = arg
                    else:
                        hi = arg
                if lo and hi:
                    word += f" from {latex_to_speech(lo)} to {latex_to_speech(hi)} of"
                elif lo:
                    word += f" over {latex_to_speech(lo)} of"
                else:
                    word += " of"
                out.append(f" {word} ")
            elif name in WRAPPERS and _group(s, i)[0].strip() in FUNCS:
                arg, i = _group(s, i)
                out.append(f" {FUNCS[arg.strip()]} ")
            elif name in ("mathrm", "operatorname") and re.fullmatch(r"[A-Z]?[a-z]+", _group(s, i)[0].strip()):
                # A name with lower case (do, softmax, Attention) is a word; an acronym (ATE, KL) is spelled.
                arg, i = _group(s, i)
                out.append(f" {arg.strip()} ")
            elif name in WRAPPERS:
                arg, i = _group(s, i)
                out.append(f" {latex_to_speech(arg) if not name.startswith('text') else arg} ")
            elif name in ("mathbb", "mathcal", "mathscr", "mathfrak"):
                arg, i = _group(s, i)
                if name == "mathbb" and arg == "E":
                    out.append(" the expectation of ")
                elif name == "mathbb" and arg in ("R", "C", "N", "Z", "Q"):
                    out.append(f" {arg} ")
                elif name == "mathcal" and arg.strip() == "N":
                    out.append(" normal ")
                elif name != "mathbb" and len(arg.strip()) == 1:
                    out.append(f" script {latex_to_speech(arg)} ")
                else:
                    out.append(f" {' '.join(arg.strip())} ")
            elif name in ACCENTS:
                arg, i = _group(s, i)
                out.append(f" {latex_to_speech(arg)} {ACCENTS[name]} ")
            elif name == "langle":
                # <x|y> is an inner product, <x| a bra, <x> an expectation.
                k = s.find("\\rangle", i)
                end = k if k >= 0 else len(s)
                parts = [p for p in re.split(r"\\vert|\\mid|\|", s[i:end])]
                if k >= 0 and len(parts) == 2:
                    out.append(f" the inner product of {latex_to_speech(parts[0])} and {latex_to_speech(parts[1])} ")
                    i = k + len("\\rangle")
                elif k >= 0 and len(parts) == 1:
                    out.append(f" the expectation of {latex_to_speech(parts[0])} ")
                    i = k + len("\\rangle")
                else:
                    m3 = re.match(r"([^|]*?)(?:\\vert|\|)", s[i:])
                    if m3:
                        out.append(f" bra {latex_to_speech(m3.group(1))} ")
                        i += len(m3.group(0))
                    else:
                        out.append(" ")
            elif name == "rangle":
                out.append(" ")
            elif name in ("lVert", "Vert", "|"):
                close = {"lVert": "\\rVert"}.get(name, "\\" + name)
                k = s.find(close, i)
                if k >= 0:
                    out.append(f" the norm of {latex_to_speech(s[i:k])} ")
                    i = k + len(close)
                else:
                    out.append(" norm ")
            elif name == "rVert":
                out.append(" ")
            elif name in ("tag", "label", "eqref", "ref", "hspace", "vspace", "phantom"):
                _, i = _group(s, i)
                out.append(" ")
            elif name in ("substack", "underbrace", "overbrace"):
                out.append(" ")
            elif name == "not":
                nxt, i = _group(s, i)
                out.append(" is not equal to " if nxt == "=" else f" not {latex_to_speech(nxt)} ")
            elif name in ("vert", "lvert"):
                # |x> is a ket, |x| an absolute value.
                m2 = re.match(r"\s*(.{0,40}?)\s*(\\rangle|\\rvert|\\vert|\|)", s[i:])
                if m2 and m2.group(2) == "\\rangle":
                    out.append(f" ket {latex_to_speech(m2.group(1))} ")
                    i += len(m2.group(0))
                elif m2 and m2.group(1).strip():
                    out.append(f" the absolute value of {latex_to_speech(m2.group(1))} ")
                    i += len(m2.group(0))
                else:
                    out.append(" bar ")
            elif name == "rvert":
                out.append(" ")
            elif name.lower() in GREEK or name.lstrip("var").lower() in GREEK:
                g = name[3:] if name.startswith("var") else name
                out.append(f" {'capital ' if g[0].isupper() else ''}{g.lower()} ")
            elif cmd in WORDS:
                out.append(WORDS[cmd])
            elif name in ("{", "}", "%", "$", "#", "_"):
                out.append(" ")
            else:
                out.append(f" {name} ")
        elif c in "_^":
            arg, i = _group(s, i + 1)
            out.append(_script(arg, c == "^"))
        elif c == "|":
            m = re.match(r"\|\s*([^|]{1,12}?)\s*\\rangle", s[i:])
            if m:
                out.append(f" ket {latex_to_speech(m.group(1))} ")
                i += len(m.group(0))
            else:
                m4 = re.match(r"\|([^|]{1,30})\|", s[i:])
                if m4:
                    out.append(f" the absolute value of {latex_to_speech(m4.group(1))} ")
                    i += len(m4.group(0))
                else:
                    out.append(" bar ")
                    i += 1
        elif c == "\x00":
            out.append(". ")
            i += 1
        elif c == "*":
            out.append(" star ")
            i += 1
        elif c in "{}()[]":
            out.append(" ")
            i += 1
        elif c == "=":
            out.append(" equals ")
            i += 1
        elif c == "+":
            out.append(" plus ")
            i += 1
        elif c in "-\u2212":
            out.append(" minus ")
            i += 1
        elif c == "<":
            out.append(" is less than ")
            i += 1
        elif c == ">":
            out.append(" is greater than ")
            i += 1
        elif c == "/":
            out.append(" over ")
            i += 1
        elif c == "'" and re.match(r"'s\b", s[i:]):
            out.append("'s ")
            i += 2
        elif c == "'":
            out.append(" prime ")
            i += 1
        elif c == "!":
            out.append(" factorial ")
            i += 1
        elif c == ",":
            out.append(", ")
            i += 1
        elif c.isalpha():
            # A run of letters in math is a product of symbols ("xy"), unless it is a word ("softmax").
            m = re.match(r"[A-Za-z]+", s[i:])
            w = m.group(0)
            if w in FUNCS:
                out.append(f" {FUNCS[w]} ")
                i += len(w)
                continue
            out.append(f" {w} " if len(w) > 3 else " " + " ".join(w) + " ")
            i += len(w)
        else:
            out.append(c)
            i += 1
    text = "".join(out)
    text = re.sub(r"\s+([,.])", r"\1", text)
    text = re.sub(r"[.,](?:\s*[.,])+", ".", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


# ---------------------------------------------------------------------------------------------------------------------
# Math through a local LLM (Ollama): a paragraph with formulas rewritten so a narrator can read it.
# ---------------------------------------------------------------------------------------------------------------------
LLM_PROMPT = ("You turn LaTeX formulas into the words a lecturer says when reading them aloud: a subscript as "
              "'sub', a superscript as 'squared' or 'to the power', a fraction as 'over', a function applied to "
              "arguments as 'of'. Do not say brackets, parentheses, commas or slashes as words. Read only what is "
              "in each formula; if one is garbled beyond reading, give 'a formula'. The paragraph is context only: "
              "do not read it. Return JSON: {\"spoken\": [one string per formula, in order]}.")


class LLM:
    """Spoken forms of the formulas of one paragraph from a local Ollama model. Only the formulas are rewritten, so
    the prose is never changed. Answers are cached in ~/.cache/text-to-audio/llm-cache.json."""

    def __init__(self, model, host="http://localhost:11434"):
        import hashlib
        import os
        self.model, self.host = model, host.rstrip("/")
        self.calls = self.fallbacks = self.cached = 0
        self.cache_path = os.path.expanduser("~/.cache/text-to-audio/llm-cache.json")
        self.salt = hashlib.sha1((model + LLM_PROMPT).encode()).hexdigest()[:8]
        try:
            with open(self.cache_path) as f:
                self.cache = json.load(f)
        except Exception:
            self.cache = {}

    def save(self):
        import os
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path + ".tmp", "w") as f:
            json.dump(self.cache, f)
        os.replace(self.cache_path + ".tmp", self.cache_path)

    def speak(self, formulas, context):
        """A list of spoken strings, one per formula, or None (the caller falls back to the rules)."""
        import hashlib
        key = self.salt + hashlib.sha1(json.dumps(formulas).encode()).hexdigest()
        if key in self.cache:
            self.cached += 1
            return self.cache[key]
        listing = "\n".join(f"{k + 1}. {f}" for k, f in enumerate(formulas))
        schema = {"type": "object", "properties": {"spoken": {"type": "array", "items": {"type": "string"}}},
                  "required": ["spoken"]}
        body = json.dumps({"model": self.model, "stream": False, "think": False, "keep_alive": "30m",
                           "format": schema, "options": {"temperature": 0, "num_ctx": 8192},
                           "messages": [{"role": "system", "content": LLM_PROMPT},
                                        {"role": "user", "content": f"Paragraph (context only):\n{context[:3000]}"
                                                                    f"\n\nFormulas:\n{listing}"}]}).encode()
        req = urllib.request.Request(self.host + "/api/chat", body, {"Content-Type": "application/json"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                spoken = json.loads(json.loads(r.read())["message"]["content"])["spoken"]
        except Exception:
            self.fallbacks += 1
            return None
        ok = (isinstance(spoken, list) and len(spoken) == len(formulas)
              and all(isinstance(x, str) and x.strip() and "\\" not in x and "$" not in x
                      and len(x) <= 12 * len(f) + 60 for x, f in zip(spoken, formulas)))
        if not ok:
            self.fallbacks += 1
            return None
        spoken = [re.sub(r"\s+", " ", x).strip() for x in spoken]
        self.cache[key] = spoken
        if self.calls % 10 == 0:
            self.save()
        return spoken


# ---------------------------------------------------------------------------------------------------------------------
# Lexicon: whole-word replacements for abbreviations and terms the voice gets wrong.
# ---------------------------------------------------------------------------------------------------------------------
def load_lexicon(paths):
    pairs = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
                    continue
                src, dst = line.split("\t", 1)
                pairs.append((src, dst))
    # Longest first, so "e.g." wins over "e".
    pairs.sort(key=lambda p: -len(p[0]))
    return [(re.compile(r"(?<![\w.])" + re.escape(s) + (r"(?!\w)" if s[-1].isalnum() else "")), d)
            for s, d in pairs]


def apply_lexicon(text, lexicon):
    # A hyphenated ALL-CAPS compound ("ON-POLICY") is read letter by letter; in lower case it is read as words.
    text = re.sub(r"\b[A-Z]{2,}(?:-[A-Z]{2,})+\b", lambda m: m.group(0).lower(), text)
    for rx, dst in lexicon:
        text = rx.sub(dst, text)
    return text


# ---------------------------------------------------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Chapter:
    title: str
    paragraphs: list = field(default_factory=list)


@dataclass
class Options:
    math: str = "rules"         # rules | llm | skip
    tables: str = "skip"        # skip | read
    code: str = "skip"          # skip | read
    chapter_level: int = 2      # headings at this level or above start a chapter
    llm: object = None
    lexicon: list = field(default_factory=list)


STATS_KEYS = ("inline_math", "display_math", "llm_rewrites", "tables", "code_blocks", "images", "links")


def _sentence(t):
    t = t.strip()
    if not t or t[-1] in ".!?:;" or (len(t) > 1 and t[-1] in "\"'\u201d\u2019)" and t[-2] in ".!?"):
        return t
    return t + "."


SMALL = set("a an and as at but by for from in into is of on or the to up via vs with without how why what who "
            "when which".split())


def _uncap(line):
    """An ALL-CAPS heading in normal case, for tidy chapter titles (the voice reads all-caps words either way).
    Short words that are not common words stay as they are (acronyms: LLM, GPU)."""
    words = re.findall(r"[A-Za-z]+", line)
    if len(words) < 2 or sum(w.isupper() for w in words) < 0.6 * len(words):
        return line

    def fix(m):
        w = m.group(0)
        if not w.isupper() or len(w) == 1:
            return w
        if w.lower() in SMALL:
            return w.lower()
        return w.capitalize() if len(w) >= 4 else w
    out = re.sub(r"[A-Za-z]+", fix, line)
    return out[0].upper() + out[1:]


def _math_inline(text, opt, stats):
    def sub(m):
        stats["inline_math"] += 1
        if opt.math == "skip":
            return "a formula"
        return latex_to_speech(m.group(1))
    return re.sub(r"(?<![\\$])\$(?!\$)([^$\n]+?)(?<!\\)\$", sub, text)


def _symbols(text):
    """Operators standing alone in prose ("theta = 1,000") as words."""
    text = re.sub(r"(?<=\S) = (?=\S)", " equals ", text)
    text = re.sub(r"(?<=\S) \+ (?=\S)", " plus ", text)
    text = re.sub(r"(?<=\S) (?:≈|~) (?=\S)", " is approximately ", text)
    text = re.sub(r"(?<=\S) (?:≤|<=) (?=\S)", " is at most ", text)
    text = re.sub(r"(?<=\S) (?:≥|>=) (?=\S)", " is at least ", text)
    return text


def _inline(text, opt, stats):
    """Inline Markdown to plain words. Math is handled before, by _paragraph."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", lambda m: (stats.__setitem__("images", stats["images"] + 1) or ""), text)
    text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)   # wiki links
    text = re.sub(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)",
                  lambda m: (stats.__setitem__("links", stats["links"] + 1) or m.group(1)), text)
    text = re.sub(r"\[\^[^\]]+\]", "", text)                                   # footnote marks
    text = re.sub(r"<(https?://[^>]+)>", "a link", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"<[^>\n]+>", " ", text)                                     # HTML tags
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|$&%])", r"\1", text)               # Markdown escapes
    text = text.replace("&nbsp;", " ").replace("&amp;", "and").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"\s*(?:->|→)\s*", " to ", text)
    text = _symbols(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


INLINE = re.compile(r"(?<![\\$])\$(?!\$)([^$\n]+?)(?<!\\)\$")


def _paragraph(text, opt, stats):
    """A paragraph of Markdown to speech. With --math llm, the paragraph's formulas go to the LLM together."""
    if opt.math == "llm" and opt.llm and "$" in text:
        formulas = INLINE.findall(text)
        spoken = opt.llm.speak(formulas, text) if formulas else None
        if spoken:
            stats["llm_rewrites"] += len(formulas)
            stats["inline_math"] += len(formulas)
            it = iter(spoken)
            text = INLINE.sub(lambda m: next(it), text)
    text = _math_inline(text, opt, stats)
    return apply_lexicon(_inline(text, opt, stats), opt.lexicon)


def _display(tex, opt, stats):
    stats["display_math"] += 1
    if opt.math == "skip":
        return "An equation is shown here."
    words = None
    if opt.math == "llm" and opt.llm:
        spoken = opt.llm.speak([tex.strip()], "(a display equation)")
        if spoken:
            stats["llm_rewrites"] += 1
            words = spoken[0]
    words = words or latex_to_speech(tex)
    if not words:
        return ""
    if len(words) > 400:
        return "A long equation is shown here."
    return _sentence("Equation: " + words)


def _table(rows, caption, opt, stats):
    stats["tables"] += 1
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]
    if opt.tables == "read" and cells:
        head, body = cells[0], cells[1:]
        lines = []
        for r in body:
            pairs = [f"{h}: {v}" if h else v for h, v in zip(head, r) if v]
            lines.append(_sentence(_paragraph(", ".join(pairs), opt, stats)))
        return lines
    n = max(len(cells) - 1, 0)
    return [f"A table with {n} row{'s' if n != 1 else ''} is skipped here."]


def prepare_markdown(src, opt, title):
    stats = dict.fromkeys(STATS_KEYS, 0)
    src = src.replace("\r\n", "\n")
    src = re.sub(r"\A---\n.*?\n---\n", "", src, flags=re.S)                 # YAML front matter
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)                        # page marks and comments
    lines = src.split("\n")
    chapters = [Chapter(title)]
    para = []

    def flush():
        if para:
            text = " ".join(l.strip() for l in para)
            para.clear()
            # A paragraph of only a margin note or a figure note is dropped (pdf-to-markdown writes them in italics).
            if re.fullmatch(r"\*\[(?:Margin note|Figure)[^\]]*\].*\*", text):
                return
            spoken = _paragraph(text, opt, stats)
            if re.search(r"\w", spoken):
                chapters[-1].paragraphs.append(_sentence(spoken))

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):                      # fenced code
            flush()
            fence = s[:3]
            lang = s[3:].strip().lower()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            body = lines[i + 1:j]
            i = j + 1
            if lang in ("math", "latex", "tex"):
                chapters[-1].paragraphs.append(_display(" ".join(body), opt, stats))
            elif opt.code == "read":
                stats["code_blocks"] += 1
                chapters[-1].paragraphs.extend(_sentence(b.strip()) for b in body if b.strip())
            else:
                stats["code_blocks"] += 1
                chapters[-1].paragraphs.append("A code listing is skipped here.")
            continue
        if s.startswith("$$"):                                               # display math
            flush()
            rest = s[2:]
            if rest.rstrip().endswith("$$") and len(rest.strip()) > 2:
                tex = rest.rstrip()[:-2]
                i += 1
            else:
                buf = [rest]
                j = i + 1
                while j < len(lines) and "$$" not in lines[j]:
                    buf.append(lines[j])
                    j += 1
                if j < len(lines):
                    buf.append(lines[j].split("$$")[0])
                tex = " ".join(buf)
                i = j + 1
            spoken = _display(tex, opt, stats)
            if spoken:
                chapters[-1].paragraphs.append(spoken)
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", s)
        if m:                                                                # heading
            flush()
            level, head = len(m.group(1)), _sentence(_uncap(_paragraph(m.group(2), opt, stats)))
            if level <= opt.chapter_level:
                if chapters[-1].paragraphs or chapters[-1].title != title:
                    chapters.append(Chapter(head.rstrip(".")))
                else:
                    chapters[-1].title = head.rstrip(".")
                chapters[-1].paragraphs.append(head)
            else:
                chapters[-1].paragraphs.append(head)
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush()                                                          # table
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            chapters[-1].paragraphs.extend(_table(lines[i:j], None, opt, stats))
            i = j
            continue
        if re.fullmatch(r"(\*\s*){3,}|(-\s*){3,}|(_\s*){3,}", s):            # horizontal rule
            flush()
            i += 1
            continue
        if not s:
            flush()
            i += 1
            continue
        lm = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if lm:                                                               # list item: its own paragraph
            flush()
            para.append(lm.group(1))
            j = i + 1
            while (j < len(lines) and lines[j].strip() and lines[j].startswith((" ", "\t"))
                   and not re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", lines[j])):
                para.append(lines[j])
                j += 1
            flush()
            i = j
            continue
        para.append(re.sub(r"^\s*>\s?", "", line))                          # body text, block quotes
        i += 1
    flush()
    return [c for c in chapters if c.paragraphs], stats


PRE = re.compile(r"\S {3,}\S")     # three spaces inside a line: columns, not prose


def _is_caps(line):
    words = re.findall(r"[A-Za-z]{2,}", line)
    return bool(words) and sum(w.isupper() for w in words) >= 0.6 * len(words) and any(len(w) >= 3 for w in words)


SEP = re.compile(r"[=\-_*#~+]{3,}")
CHAPTER_WORD = re.compile(r"^(?:CHAPTER|Chapter|PART|Part|APPENDIX|Appendix)\s+[\dIVXLA-Z]+\b")


def _text_headings(lines):
    """{line index: (major, number of lines it takes)} for the headings of a plain-text file. A heading stands alone
    (a blank or a rule line above it) and is short. Major: underlined with ===, set between === rules, or starting
    with CHAPTER / PART / APPENDIX. Minor: underlined with ---, or an ALL-CAPS line standing alone."""
    heads = {}
    n = len(lines)
    for k, line in enumerate(lines):
        s = line.strip()
        if not s or SEP.fullmatch(s) or len(s) > 100 or line.startswith((" ", "\t")):
            continue
        prev = lines[k - 1].strip() if k else ""
        nxt = lines[k + 1].strip() if k + 1 < n else ""
        if prev and not SEP.fullmatch(prev):
            continue
        if re.fullmatch(r"={3,}", nxt):
            heads[k] = (True, 2)
        elif re.fullmatch(r"-{3,}", nxt):
            heads[k] = (bool(CHAPTER_WORD.match(s)), 2)
        elif (not nxt or SEP.fullmatch(nxt)) and (_is_caps(s) or CHAPTER_WORD.match(s)) and not s.endswith(","):
            major = bool(CHAPTER_WORD.match(s)) or (re.fullmatch(r"={3,}", prev) is not None
                                                    and re.fullmatch(r"={3,}", nxt) is not None)
            heads[k] = (major, 1)
    return heads


def prepare_text(src, opt, title):
    """Plain text: paragraphs split by blank lines, hard-wrapped lines joined. Headings (see _text_headings) start
    chapters: the major ones when there are two or more, else the minor ones too. Rule lines are dropped. A block of
    column-aligned lines (a table or a listing) is skipped with a notice, or read with --tables read."""
    stats = dict.fromkeys(STATS_KEYS, 0)
    lines = src.replace("\r\n", "\n").split("\n")
    heads = _text_headings(lines)
    minor_chapters = sum(1 for major, _ in heads.values() if major) < 2
    chapters = [Chapter(title)]
    para = []

    def flush():
        if para:
            text = " ".join(l.strip() for l in para)
            para.clear()
            text = apply_lexicon(_symbols(re.sub(r"https?://\S+", "a link", text)), opt.lexicon)
            if re.search(r"\w", text):
                chapters[-1].paragraphs.append(_sentence(text))

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if i in heads:
            flush()
            major, span = heads[i]
            head = _sentence(_uncap(apply_lexicon(s, opt.lexicon)))
            if major or minor_chapters:
                if chapters[-1].paragraphs:
                    chapters.append(Chapter(head.rstrip(".")))
                else:
                    chapters[-1].title = head.rstrip(".")
            chapters[-1].paragraphs.append(head)
            i += span
            continue
        if not s or SEP.fullmatch(s):
            flush()
            i += 1
            continue
        if PRE.search(line):                                                 # an aligned table or a listing
            flush()
            j = i
            while j < len(lines) and lines[j].strip() and (PRE.search(lines[j]) or re.fullmatch(r"[\s\-=+|]+", lines[j])):
                j += 1
            block = [l for l in lines[i:j] if not re.fullmatch(r"[\s\-=+|]+", l)]
            stats["tables"] += 1
            if opt.tables == "read":
                chapters[-1].paragraphs.extend(_sentence(apply_lexicon(re.sub(r"\s{2,}", ", ", l.strip()),
                                                                       opt.lexicon)) for l in block)
            else:
                chapters[-1].paragraphs.append("A table or listing is skipped here.")
            i = j
            continue
        # A bullet, a numbered item or a "LABEL:" line starts a new paragraph; an indented continuation joins it.
        if re.match(r"^\s*(?:[-*+\u2022]|\d+[.)]|[A-Z][A-Z ]{2,}:)\s+", line) and para:
            flush()
        para.append(re.sub(r"^\s*[-*+\u2022]\s+", "", line))
        i += 1
    flush()
    return [c for c in chapters if c.paragraphs], stats


def prepare(path, opt, title=None, markdown=None):
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    if markdown is None:
        markdown = path.lower().endswith((".md", ".markdown", ".mdown"))
    title = title or re.sub(r"[_\-]+", " ", re.sub(r"\.[^.]+$", "", path.rsplit("/", 1)[-1])).strip()
    return (prepare_markdown if markdown else prepare_text)(src, opt, title)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--math", default="rules", choices=["rules", "skip"])
    ap.add_argument("--tables", default="skip", choices=["skip", "read"])
    ap.add_argument("--code", default="skip", choices=["skip", "read"])
    a = ap.parse_args()
    chapters, stats = prepare(a.file, Options(math=a.math, tables=a.tables, code=a.code))
    for c in chapters:
        print(f"=== {c.title}")
        print("\n\n".join(c.paragraphs))
        print()
    print(json.dumps(stats), file=sys.stderr)


if __name__ == "__main__":
    main()
