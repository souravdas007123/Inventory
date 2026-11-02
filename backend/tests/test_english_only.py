"""The app speaks English only: no Hinglish words in templates or code (a user who sees one reports it as a bug)."""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
WORDS = re.compile(
    r"\b(karo|karna|kijiye|chahiye|chahie|nahin|karein|dalein|daalein|chadhayein|dekhein|dekho|aapka|aapki|aapke|apna|apni|wala|wali|"
    r"kya|kaise|kyun|hoga|hogi|raha|rahe|rahi|sakte|sakta|bhejein|dusri|dusra|abhi|turant|jaldi|mein|lekin|isme|uska|uski|zaroor|"
    r"zaruri|jaroori|galat|dhyan|shukriya|dhanyavaad|baaki|sabhi|yahan|milega|batao|dikhao|rakhein|banayein|bharein|chunein|"
    r"likhein|dijiye|karke)\b", re.I)


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
                        found.append(f'{p.relative_to(ROOT)}:{n}: {line.strip()[:80]}')
        self.assertEqual(found, [], 'Hinglish found:\n' + '\n'.join(found))

    def test_the_check_itself_catches_hinglish(self):
        self.assertTrue(WORDS.search('Cancel / dusri file'))
        self.assertFalse(WORDS.search('Cancel / choose another file'))
