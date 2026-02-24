"""
Веб-панель администратора
Запуск: python web/app.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request, redirect, url_for, session, send_from_directory
from functools import wraps
import json
from datetime import datetime

from database import db
from config import config

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.getenv("WEB_SECRET_KEY", "change_this_secret_key_123")

WEB_PASSWORD = os.getenv("WEB_PASSWORD", "admin123")  # Смените в .env!

# ---- AUTH ----

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == WEB_PASSWORD:
            session['logged_in'] = True
            return redirect('/')
        error = "Неверный пароль"
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ---- API ENDPOINTS ----

@app.route('/api/stats')
@login_required
def api_stats():
    users = db.get_users_count()
    products = db.get_all_products()
    orders = db.get_orders(limit=1000)
    categories = db.get_categories()
    messages = db.get_all_messages(limit=1000)

    # Статистика по категориям
    cats_data = {}
    for p in products:
        cat = p['category_name']
        cats_data[cat] = cats_data.get(cat, 0) + 1

    return jsonify({
        'users': users,
        'products': len(products),
        'orders': len(orders),
        'categories': len(categories),
        'messages': len(messages),
        'cats_breakdown': cats_data,
        'recent_orders': orders[:5],
    })


@app.route('/api/products')
@login_required
def api_products():
    products = db.get_all_products()
    return jsonify(products)


@app.route('/api/products/<int:product_id>', methods=['DELETE'])
@login_required
def api_delete_product(product_id):
    with db._conn() as conn:
        conn.execute("UPDATE products SET is_available=0 WHERE id=?", (product_id,))
    return jsonify({'ok': True})


@app.route('/api/products', methods=['POST'])
@login_required
def api_add_product():
    data = request.json
    from parser import ManualParser
    product_id = ManualParser.add_product(
        name=data['name'],
        price=float(data['price']),
        category=data['category'],
        description=data.get('description'),
    )
    product = db.get_product(product_id)
    return jsonify(product)


@app.route('/api/categories')
@login_required
def api_categories():
    return jsonify(db.get_categories())


@app.route('/api/categories/<int:cat_id>/markup', methods=['POST'])
@login_required
def api_update_markup(cat_id):
    markup = float(request.json['markup'])
    db.update_category_markup(cat_id, markup)
    return jsonify({'ok': True, 'markup': markup})


@app.route('/api/categories', methods=['POST'])
@login_required
def api_add_category():
    data = request.json
    cat_id = db.upsert_category(
        name=data['name'],
        emoji=data.get('emoji', '📦'),
        markup=float(data.get('markup', 15))
    )
    return jsonify({'id': cat_id, **data})


@app.route('/api/users')
@login_required
def api_users():
    return jsonify(db.get_all_users())


@app.route('/api/messages')
@login_required
def api_messages():
    user_id = request.args.get('user_id')
    if user_id:
        msgs = db.get_user_messages(int(user_id), limit=100)
    else:
        msgs = db.get_all_messages(limit=200)
    return jsonify(msgs)


@app.route('/api/orders')
@login_required
def api_orders():
    return jsonify(db.get_orders(limit=100))


@app.route('/api/orders/<int:order_id>/status', methods=['POST'])
@login_required
def api_update_order_status(order_id):
    status = request.json['status']
    with db._conn() as conn:
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    return jsonify({'ok': True})


# ---- PAGES ----

@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/products')
@login_required
def products_page():
    return render_template('products.html')


@app.route('/categories')
@login_required
def categories_page():
    return render_template('categories.html')


@app.route('/users')
@login_required
def users_page():
    return render_template('users.html')


@app.route('/messages')
@login_required
def messages_page():
    return render_template('messages.html')


@app.route('/orders')
@login_required
def orders_page():
    return render_template('orders.html')


if __name__ == '__main__':
    db.init()
    print("🌐 Web admin panel running at http://localhost:5000")
    print(f"🔑 Password: {WEB_PASSWORD}")
    app.run(host='0.0.0.0', port=5000, debug=True)
