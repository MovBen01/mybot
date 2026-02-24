"""
Глобальный экземпляр парсера — импортируется из admin.py без циклических зависимостей
"""
_parser = None

def set_parser(p):
    global _parser
    _parser = p

def get_parser():
    return _parser
