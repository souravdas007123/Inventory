"""The app speaks English only: no Hinglish words in templates or code (a user who sees one reports it as a bug)."""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
WORDS = re.compile(
    r"\b(karo|karna|kijiye|chahiye|chahie|nahin|karein|dalein|daalein|chadhayein|dekhein|dekho|aapka|aapki|aapke|apna|apni|wala|wali|"
    r"kya|kaise|kyun|hoga|hogi|raha|rahe|rahi|sakte|sakta|bhejein|dusri|dusra|abhi|turant|jaldi|mein|lekin|isme|uska|uski|zaroor|"
    r"zaruri|jaroori|galat|dhyan|shukriya|dhanyavaad|baaki|sabhi|yahan|milega|batao|dikhao|rakhein|banayein|bharein|chunein|"
    r"likhein|dijiye|karke|wajah|nahi|kuch|kaam|naya|nayi|purana|purani|dukaan|dukan|grahak|kripya|theek|thik|accha|acha|bahut|bohot|"
    r"hai|aur|toh|aap|mera|meri|kitna|kitne|kahan|jao|ruko|bhejo|dekhiye|batayein|samajh|madad|jankari|tarika|galti)\b", re.I)


# Words the importer must UNDERSTAND when a shop types them in its own Excel file (input, never shown on screen).
ACCEPTED_AS_INPUT = {('backend/tabular.py', "'nahi'")}


class EnglishOnlyTests(SimpleTestCase):
    def test_no_hinglish_in_templates_or_code(self):
        found = []
        for folder in ('templates', 'backend', 'tenants'):
            for p in (ROOT / folder).rglob('*'):
                if p.suffix not in ('.html', '.py', '.js', '.txt') or 'migrations' in p.parts or '__pycache__' in p.parts:
                    continue
                if 'tests' in p.parts or p.name.startswith('test_'):
                    continue
                for n, line in enumerate(p.read_text(encoding='utf-8', errors='ignore').splitlines(), 1):
                    if WORDS.search(line):
                        if any(str(p.relative_to(ROOT)).replace('\\', '/') == f and token in line for f, token in ACCEPTED_AS_INPUT):
                            continue
                        found.append(f'{p.relative_to(ROOT)}:{n}: {line.strip()[:80]}')
        self.assertEqual(found, [], 'Hinglish found:\n' + '\n'.join(found))

    def test_the_check_itself_catches_hinglish(self):
        self.assertTrue(WORDS.search('Cancel / dusri file'))
        self.assertFalse(WORDS.search('Cancel / choose another file'))
        for word in ('Wajah', 'nahi', 'kaam', 'Dukan', 'hai'):
            self.assertTrue(WORDS.search(f'<th>{word}</th>'), word)


MISSPELLINGS = re.compile(
    r"\b(cetrizine|recieve\w*|seperat\w*|occured|definate\w*|adress\w*|pasword\w*|passowrd|succesful\w*|sucess\w*|expence\w*|medecine\w*|"
    r"quanity|quantiy|invioce\w*|discout|emial|teh|wich|alot|dont|cant|doesnt|isnt|thier|untill|calender|begining|neccessar\w*|"
    r"necesary|accomodat\w*|acheive\w*|wierd|beleive|immediatly|supplyer\w*|customar|purchse|totla|amout)\b", re.I)


class SpellingTests(SimpleTestCase):
    """A dictionary scan of all screen text found these; this keeps the common ones from coming back."""

    def test_no_known_misspellings_on_screen(self):
        found = []
        for folder in ('templates', 'backend', 'tenants'):
            for p in (ROOT / folder).rglob('*'):
                if p.suffix not in ('.html', '.py', '.js', '.txt') or 'migrations' in p.parts or '__pycache__' in p.parts:
                    continue
                if 'tests' in p.parts or p.name.startswith('test_'):
                    continue
                for n, line in enumerate(p.read_text(encoding='utf-8', errors='ignore').splitlines(), 1):
                    if MISSPELLINGS.search(line):
                        found.append(f'{p.relative_to(ROOT)}:{n}: {MISSPELLINGS.search(line).group(0)}')
        self.assertEqual(found, [], 'Misspelled words found:\n' + '\n'.join(found))

    def test_the_check_itself_catches_them(self):
        for bad in ('Cetrizine 10', 'You will recieve it', 'seperate file', 'dont forget'):
            self.assertTrue(MISSPELLINGS.search(bad), bad)
        for good in ('Cetirizine 10', 'You will receive it', 'separate file', "don't forget"):
            self.assertFalse(MISSPELLINGS.search(good), good)
