from app.voice.speech_text import SentenceSplitter, speakable
from app.voice.stt import clean


def test_speakable_portuguese():
    spoken = speakable("A limpeza com a **Dra. Ana** custa R$ 180.", "pt")
    assert spoken == "A limpeza com a Doutora Ana custa 180 reais."
    assert speakable("Quinta às 8h30 ou 14h15?", "pt") == "Quinta às 8 e meia ou 14 e 15?"
    assert speakable("R$ 1.200,50", "pt") == "1200 reais e 50 centavos"
    assert speakable("R$ 90,00", "pt") == "90 reais"


def test_speakable_english():
    assert speakable("Dr. Paulo is free at 8 am.", "en") == "Doctor Paulo is free at 8 am."
    # English replies don't use 8h30, and the rewrite only applies in Portuguese
    assert speakable("Room 8h30", "en") == "Room 8h30"


def test_splitter_streams_whole_sentences():
    splitter = SentenceSplitter()
    out = []
    for delta in ["Claro! Tenho quinta com a Dra", ". Ana às 8h. Ou ", "sexta às 10h. Qual ", "prefere?"]:
        out += splitter.push(delta)
    assert out == ["Claro!", "Tenho quinta com a Dra. Ana às 8h.", "Ou sexta às 10h."]
    assert splitter.flush() == "Qual prefere?"
    assert splitter.flush() is None


def test_splitter_waits_for_the_space_after_a_period():
    splitter = SentenceSplitter()
    # "R$ 1." could still become "R$ 1.200"
    assert splitter.push("Custa R$ 1.") == []
    assert splitter.push("200. Pode ser?") == ["Custa R$ 1.200."]


def test_transcript_cleanup():
    result = {
        "language": "Portuguese",
        "segments": [
            {"text": " Quero marcar uma limpeza.", "no_speech_prob": 0.01},
            {"text": " Obrigado.", "no_speech_prob": 0.9},
        ],
    }
    assert clean(result).text == "Quero marcar uma limpeza."
    assert clean(result).language == "pt"
    noise = {"language": "pt", "segments": [{"text": "Legendas pela comunidade Amara.org", "no_speech_prob": 0.2}]}
    assert clean(noise).text == ""
    assert clean({"language": "japanese", "text": "はい"}).language is None


def test_a_long_first_sentence_starts_at_its_first_clause():
    splitter = SentenceSplitter()
    out = []
    for word in "Tem estacionamento com manobrista, R$ 25 por duas horas, e uma rua ao lado. Algo mais?".split(" "):
        out += splitter.push(word + " ")
    # Only the first piece is cut short; after that, whole sentences
    assert out == ["Tem estacionamento com manobrista,", "R$ 25 por duas horas, e uma rua ao lado.", "Algo mais?"]
    # A comma inside a number or too early on doesn't count
    assert SentenceSplitter().push("Custa R$ 1.200,50 no total, ") == ["Custa R$ 1.200,50 no total,"]
    assert SentenceSplitter().push("Sim, claro") == []
