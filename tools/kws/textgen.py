"""Random ham-radio style text with NATO callsigns, and its keyword labels."""
import random, re

NATO = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliett",
        "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango",
        "uniform", "victor", "whiskey", "x-ray", "yankee", "zulu"]
DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
VOCAB = NATO + DIGITS + ["repeater"]       # label i = VOCAB[i]; blank = len(VOCAB)
SHOW = [w[0].upper() for w in NATO] + [str(i) for i in range(10)] + ["~"]
import os as _os
LETTERS = ["letter-" + c for c in "abcdefghijklmnopqrstuvwxyz"]
if _os.environ.get("KWS_LETTERS"):                 # plain spoken letters ("w seven e c g") as extra classes
    VOCAB = VOCAB + LETTERS
    SHOW = SHOW + list("abcdefghijklmnopqrstuvwxyz")
ALIASES = {"niner": "nine", "xray": "x-ray", "juliet": "juliett", "whisky": "whiskey"}
IDX = {w: i for i, w in enumerate(VOCAB)}
LET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

NAMES = ["Jeff", "Bob", "Linda", "Dave", "Sue", "Tom", "Karen", "Rick", "Ann", "Steve", "Paul", "Carol",
         "Gary", "Jim", "Pat", "Ron", "Mike", "Don", "Barb", "Chris"]
PLACES = ["Lynden", "Bellingham", "Ferndale", "Everson", "Blaine", "Sumas", "Mount Vernon", "Seattle",
          "Abbotsford", "Burlington", "Whatcom County", "Lake Whatcom", "Birch Bay"]
FILL = [
    "go ahead", "monitoring", "clear", "for ID", "listening", "QSL", "seventy three", "thanks for the contact",
    "you're full quieting", "you're a little scratchy", "good copy", "say again", "standing by",
    "how copy", "roger that", "the net starts at seven", "weather is clear and cold tonight",
    "this is a test", "repeater test", "testing", "back to you", "have a good evening", "over",
    "I'll be on the air later", "breaking in", "the club meeting is on Tuesday", "radio check",
    "signal report please", "you're about twenty over nine", "eleven meters", "twenty twenty one",
    "thirty", "forty four", "the frequency is four four two point zero two five",
    "minus offset, tone one one zero nine", "a hundred and three", "it's sixty degrees", "fifteen minutes",
    "anyone around", "nothing heard", "I'm heading home", "on the highway", "near the river",
    "the antenna is up on the tower", "batteries are low", "talk to you tomorrow", "what's your QTH",
    "I have to go now", "sure, sounds good", "maybe next week", "the coffee is ready",
    "please identify", "unidentified station", "emergency traffic only", "is this frequency in use",
]
TEMPL = [
    "This is {c}.", "{c} repeater.", "{c} portable.", "{c} mobile.", "{c}, {f}.", "{f}, {c}.",
    "{c} for ID.", "Calling {c}, this is {c2}.", "{c2}, {c}, {f}.", "Hello, this is {n}, {c}.",
    "{c} repeater, {p}.", "This is {c}, {f}.", "{c}, {f}, {c} clear.", "{f}. {c}.", "{c}, go ahead.",
    "Good evening, {c} repeater in {p}.", "{c} {c2}", "{c}", "{s}", "{s}, {s2}", "{f}.", "{f}, {f2}.",
    "{c}, net control.", "{n} here, {c}, {f}.",
    "{i}.", "{i}.", "{i}", "{i}. {i2}.", "{s}.", "{s}",
]

def callsign(rng):
    r = rng.random()
    if r < 0.18:                           # Canada
        pre = rng.choice(["VE7", "VA7", "VE6", "VA3"])
        return pre + "".join(rng.choice(LET) for _ in range(rng.choice([2, 3])))
    p = rng.choice(["K", "W", "N", "A", "KD", "KE", "KF", "KG", "KI", "AG", "AE", "AF", "WA", "WB", "N",
                    "K", "W"])
    if len(p) == 1 and rng.random() < 0.3:
        p += rng.choice(LET)
    return p + str(rng.randrange(10)) + "".join(rng.choice(LET) for _ in range(rng.choice([1, 2, 3, 3])))

def spell_seq(chars, rng):
    sep = rng.choice([" ", " ", ", "])
    out = []
    for ch in chars:
        if ch.isdigit():
            w = DIGITS[int(ch)]
            if ch == "9" and rng.random() < 0.4: w = "niner"
            out.append(w)
        else:
            w = NATO[ord(ch) - 65]
            out.append(w.capitalize())
    return sep.join(out)

def spell(cs, rng):
    return spell_seq(cs, rng)

def rand_letters(rng):
    n = rng.randint(2, 7)
    s = "".join(rng.choice(LET + "0123456789" if rng.random() < 0.3 else LET) for _ in range(n))
    return spell_seq(s, rng)

def iso(rng):
    n = rng.choice([1, 1, 1, 2])
    return spell_seq("".join(rng.choice(LET + "0123456789") for _ in range(n)), rng)

def sentence(rng):
    t = rng.choice(TEMPL)
    return t.format(c=spell(callsign(rng), rng), c2=spell(callsign(rng), rng), f=rng.choice(FILL),
                    f2=rng.choice(FILL), n=rng.choice(NAMES), p=rng.choice(PLACES),
                    s=rand_letters(rng), s2=rand_letters(rng), i=iso(rng), i2=iso(rng))

def labels(text):
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text.lower()):
        w = ALIASES.get(w, w)
        if w in IDX: out.append(IDX[w])
    return out

def show(lab):
    return "".join(SHOW[i] for i in lab)

if __name__ == "__main__":
    rng = random.Random(1)
    for _ in range(12):
        s = sentence(rng); print(s, "->", show(labels(s)))
