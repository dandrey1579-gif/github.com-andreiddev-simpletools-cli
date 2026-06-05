# simpletools

Утилита командной строки и веб-интерфейс для управления инфраструктурой через текстовые команды.

Модель: **WISH → SEE → SAY**

## Возможности

- Управление GitHub-репозиториями (создание / удаление)
- Управление AWS S3-бакетами (создание / удаление)
- План выполнения до запуска (SEE)
- Подтверждение перед выполнением (SAY)
- Проверка политик безопасности (Policy Engine)
- История операций в SQLite
- Reconciliation Worker (обнаружение дрейфа)
- Веб-интерфейс (Flask)

## Установка

```bash
pip install -r requirements.txt
git clone https://github.com/andreiddev/simpletools-cli.git
cd simpletools-cli
