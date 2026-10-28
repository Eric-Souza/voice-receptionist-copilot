from app.voice.speech_text import SentenceSplitter, speakable
from app.voice.stt import clean


def test_speakable():
    assert speakable("A cleaning with **Dr. Reed** is $120.") == "A cleaning with Doctor Reed is 120 dollars."
    assert speakable("$1,200.50") == "1200 dollars and 50 cents"
    assert speakable("$1.00 or $90.00") == "1 dollar or 90 dollars"
    assert speakable("It's 8:30 am.") == "It's 8:30 am."
    # Without the comma, and with odd cents or spaces, as models write them
    assert speakable("$1100 in total, $75.5") == "1100 dollars in total, 75 dollars and 50 cents"
    assert speakable("At 8 am") == "At 8 am"


def test_splitter_streams_whole_sentences():
    splitter = SentenceSplitter()
    out = []
    for delta in ["Sure! I have Thursday with Dr", ". Reed at 8 am. Or ", "Friday at 10. Which ", "works better?"]:
        out += splitter.push(delta)
    assert out == ["Sure!", "I have Thursday with Dr. Reed at 8 am.", "Or Friday at 10."]
    assert splitter.flush() == "Which works better?"
    assert splitter.flush() is None


def test_splitter_waits_for_the_space_after_a_period():
    splitter = SentenceSplitter()
    # "$1." could still become "$1.50"
    assert splitter.push("It's $1.") == []
    assert splitter.push("50 a day. OK?") == ["It's $1.50 a day."]


def test_transcript_cleanup():
    result = {
        "segments": [
            {"text": " I'd like to book a cleaning.", "no_speech_prob": 0.01},
            {"text": " Thank you.", "no_speech_prob": 0.9},
        ],
    }
    assert clean(result).text == "I'd like to book a cleaning."
    noise = {"segments": [{"text": "Thanks for watching!", "no_speech_prob": 0.2}]}
    assert clean(noise).text == ""
    assert clean({"text": " Hello? "}).text == "Hello?"


def test_a_long_first_sentence_starts_at_its_first_clause():
    splitter = SentenceSplitter()
    out = []
    reply = "There's free parking in the garage, on levels 2 and 3, and meters on the street. Anything else?"
    for word in reply.split(" "):
        out += splitter.push(word + " ")
    # Only the first piece is cut short; after that, whole sentences
    assert out == [
        "There's free parking in the garage,",
        "on levels 2 and 3, and meters on the street.",
        "Anything else?",
    ]
    # A comma inside a number or too early on doesn't count
    assert SentenceSplitter().push("A root canal is $1,100 in total, ") == ["A root canal is $1,100 in total,"]
    assert SentenceSplitter().push("Yes, sure") == []
