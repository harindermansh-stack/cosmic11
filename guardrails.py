"""guardrails.py - care for the person asking, before any astrology is done.

1. Distress: if a question suggests the person may be in crisis, no reading is given.
   Instead they get a caring message and a free 24x7 helpline.
2. Death, lifespan and accident timing: never predicted. Such predictions only create fear.
3. Every reading ends with a short, honest disclaimer.
"""
import re

DISTRESS = [
    r"suicid", r"kill myself", r"end my life", r"end it all", r"want to die", r"wanna die", r"no reason to live",
    r"don'?t want to live", r"self[\s-]?harm", r"hurt myself", r"can'?t go on", r"cannot go on", r"better off dead",
    r"khudkushi", r"marna chah", r"mar jaana chah", r"jeena nahi", r"zinda nahi rehna",
    "आत्महत्या", "खुदकुशी", "मरना चाहत", "जीना नहीं", "जीने की इच्छा नहीं",
]
DEATH_TIMING = [
    r"when will i die", r"when i will die", r"my death", r"date of (my )?death", r"how long will i live",
    r"life ?span", r"longevity", r"will i die", r"accident (date|time|year)", r"when will .{0,20}accident",
    r"kab marunga", r"kab marungi", r"meri maut", r"mrityu", r"maut kab",
    "मृत्यु", "मौत कब", "कब मरूंगा", "कब मरूंगी", "आयु कितनी",
]

CARE_EN = ("It sounds like you may be going through something very painful right now, and you deserve real support, "
           "not an astrology reading. Please talk to someone today. In India you can call Tele-MANAS on 14416 "
           "(free, 24 hours a day, in many languages). If you are in immediate danger, call 112. "
           "Talking to a trusted friend or family member can also help. You are not alone.")
CARE_HI = ("ऐसा लगता है कि आप इस समय किसी बहुत कठिन दौर से गुज़र रहे हैं। आपको ज्योतिषीय उत्तर नहीं, सच्चे सहारे की ज़रूरत है। "
           "कृपया आज ही किसी से बात करें। भारत में आप Tele-MANAS को 14416 पर कॉल कर सकते हैं (निःशुल्क, 24 घंटे, कई भाषाओं में)। "
           "अगर तुरंत ख़तरा हो, तो 112 पर कॉल करें। किसी भरोसेमंद मित्र या परिवार के सदस्य से बात करना भी मदद करता है। आप अकेले नहीं हैं।")
DEATH_EN = ("Cosmic 11 does not predict death, lifespan or accidents. No honest reading can, and such predictions only create fear. "
            "If you are worried about your health or safety, please speak with a doctor. You are welcome to ask about any other part of your life.")
DEATH_HI = ("Cosmic 11 मृत्यु, आयु या दुर्घटना की भविष्यवाणी नहीं करता। कोई भी ईमानदार ज्योतिषीय उत्तर ऐसा नहीं कर सकता, और ऐसी भविष्यवाणियाँ केवल डर पैदा करती हैं। "
            "अगर आपको अपने स्वास्थ्य या सुरक्षा की चिंता है, तो कृपया डॉक्टर से बात करें। जीवन के किसी अन्य पहलू के बारे में आप ज़रूर पूछ सकते हैं।")
DISCLAIMER_EN = ("Note: this reading is based on classical astrology texts and is meant for reflection. "
                 "It is not medical, legal or financial advice, and no outcome is guaranteed.")
DISCLAIMER_HI = ("नोट: यह उत्तर शास्त्रीय ज्योतिष ग्रंथों पर आधारित है और चिंतन के लिए है। "
                 "यह चिकित्सा, कानूनी या वित्तीय सलाह नहीं है, और किसी भी परिणाम की गारंटी नहीं है।")


def _hit(patterns, text):
    t = text.lower()
    return any(re.search(p, t) if p.isascii() else (p in text) for p in patterns)


def check_question(question):
    """Returns None if a reading may be given, else {"kind", "en", "hi"}."""
    if _hit(DISTRESS, question):
        return {"kind": "care", "en": CARE_EN, "hi": CARE_HI}
    if _hit(DEATH_TIMING, question):
        return {"kind": "death_timing", "en": DEATH_EN, "hi": DEATH_HI}
    return None


def disclaimer(lang):
    return DISCLAIMER_HI if lang == "hi" else DISCLAIMER_EN
