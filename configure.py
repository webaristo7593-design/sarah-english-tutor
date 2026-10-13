"""First-run setup. Writes only a local .env, never sends or prints the key."""
from getpass import getpass
from pathlib import Path
import os


def main():
    root = Path(__file__).resolve().parent
    target = root / '.env'
    if target.exists():
        print('Файл .env уже существует. Для смены ключа отредактируйте его вручную.')
        return
    print('Создайте свой Gemini API key: https://aistudio.google.com/apikey')
    print('Использование API зависит от квот и тарифа вашего Google-проекта.')
    key = getpass('Вставьте ключ (символы не показываются): ').strip()
    if not key or any(char.isspace() for char in key) or any(c in key for c in '\'"#'):
        raise SystemExit('Некорректный ключ. Ничего не сохранено.')
    template = (root / '.env.example').read_text(encoding='utf-8')
    with target.open('x', encoding='utf-8') as output:
        output.write(template.replace('YOUR_GEMINI_API_KEY_HERE', key))
    if os.name != 'nt':
        target.chmod(0o600)
    print('Готово. Ключ сохранён только в локальном .env.')


if __name__ == '__main__':
    main()
