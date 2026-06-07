import os
import hashlib
from pathlib import Path
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import markdown
import bleach
from models import db, User, Book, Genre, Cover, Review, Role

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-12345'

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///library.db'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'covers')
app.config['PER_PAGE'] = 3  # По 3 книги на страницу для пагинации

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    flash('Для выполнения данного действия необходимо пройти процедуру аутентификации', 'danger')
    return redirect(url_for('login'))

def check_rights(roles_allowed):
    if not current_user.is_authenticated:
        return False
    return current_user.role_id in roles_allowed

# --- МАРШРУТЫ ПРИЛОЖЕНИЯ ---

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    
    title = request.args.get('title', '')
    author = request.args.get('author', '')
    genres_sel = request.args.getlist('genres')
    
    # Считываем выбранные года из мультиселекта
    years_sel = request.args.getlist('years')
    
    pages_from = request.args.get('pages_from', '')
    pages_to = request.args.get('pages_to', '')

    query = Book.query

    if title: query = query.filter(Book.title.like(f"%{title}%"))
    if author: query = query.filter(Book.author.like(f"%{author}%"))
    if genres_sel: query = query.join(Book.genres).filter(Genre.id.in_(genres_sel))
    
    # Фильтрация по конкретным годам из базы данных
    if years_sel: query = query.filter(Book.year.in_([int(y) for y in years_sel]))
        
    try:
        if pages_from: query = query.filter(Book.pages >= int(pages_from))
        if pages_to: query = query.filter(Book.pages <= int(pages_to))
    except ValueError:
        pass

    query = query.order_by(Book.year.desc())
    
    pagination = query.paginate(page=page, per_page=app.config['PER_PAGE'], error_out=False)
    books = pagination.items

    for book in books:
        revs = book.reviews
        book.avg_rating = round(sum(r.rating for r in revs) / len(revs), 2) if revs else 0
        book.rev_count = len(revs)

    all_genres = Genre.query.all()
    
    # 🌟 ТРЕБОВАНИЕ ТЗ: Формируем варианты годов СТРОГО исходя из содержимого БД
    # Выбираем только уникальные года существующих книг и сортируем их по убыванию
    all_years = [row[0] for row in db.session.query(Book.year).distinct().order_by(Book.year.desc()).all() if row[0]]

    return render_template('index.html', books=books, pagination=pagination, all_genres=all_genres, all_years=all_years)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_val = request.form.get('login')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        
        user = User.query.filter_by(login=login_val).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            return redirect(url_for('index'))
        
        flash('Невозможно аутентифицироваться с указанными логином и паролем', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/book/new', methods=['GET', 'POST'])
@login_required
def add_book():
    if not check_rights([1]):
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    genres = Genre.query.all()
    if request.method == 'POST':
        try:
            title = request.form.get('title')
            description = bleach.clean(request.form.get('description') or '')
            year_raw = request.form.get('year')
            publisher = request.form.get('publisher')
            author = request.form.get('author')
            pages_raw = request.form.get('pages')
            genre_ids = request.form.getlist('genres')
            
            if not title or not author or not publisher or not year_raw or not pages_raw:
                flash('Пожалуйста, заполните все обязательные поля формы.', 'danger')
                return render_template('book_form.html', action='add', genres=genres, book=None)

            year = int(year_raw)
            pages = int(pages_raw)
            
            file = request.files.get('cover')
            if not file or file.filename == '':
                flash('Загрузка обложки обязательна для добавления книги.', 'danger')
                return render_template('book_form.html', action='add', genres=genres, book=None)

            file_bytes = file.read()
            md5_hash = hashlib.md5(file_bytes).hexdigest()
            file.seek(0)

            book = Book(title=title, description=description, year=year, publisher=publisher, author=author, pages=pages)
            
            for g_id in genre_ids:
                g = db.session.get(Genre, int(g_id))
                if g: book.genres.append(g)

            db.session.add(book)
            db.session.flush()

            upload_path = Path(app.config['UPLOAD_FOLDER'])
            if not upload_path.exists():
                upload_path.mkdir(parents=True, exist_ok=True)

            existing_cover = Cover.query.filter_by(md5_hash=md5_hash).first()
            if existing_cover:
                filename = existing_cover.filename
                mime_type = existing_cover.mime_type
            else:
                filename = secure_filename(f"{book.id}_{file.filename}")
                mime_type = file.mimetype
                file.save(str(upload_path / filename))

            cover = Cover(filename=filename, mime_type=mime_type, md5_hash=md5_hash, book_id=book.id)
            db.session.add(cover)
            
            db.session.commit()
            flash('Книга успешно добавлена в библиотеку!', 'success')
            return redirect(url_for('index'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Системная ошибка при сохранении: {str(e)}', 'danger')
            
    return render_template('book_form.html', action='add', genres=genres, book=None)

@app.route('/book/<int:book_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_book(book_id):
    if not check_rights([1, 2]):
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    book = db.session.get(Book, book_id)
    if not book: return redirect(url_for('index'))
    genres = Genre.query.all()
    
    if request.method == 'POST':
        try:
            book.title = request.form.get('title')
            book.description = bleach.clean(request.form.get('description') or '')
            book.year = int(request.form.get('year'))
            book.publisher = request.form.get('publisher')
            book.author = request.form.get('author')
            book.pages = int(request.form.get('pages'))
            
            genre_ids = request.form.getlist('genres')
            book.genres = []
            for g_id in genre_ids:
                g = db.session.get(Genre, int(g_id))
                if g: book.genres.append(g)
            
            db.session.commit()
            flash('Данные книги успешно обновлены!', 'success')
            return redirect(url_for('view_book', book_id=book.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка обновления: {str(e)}', 'danger')

    return render_template('book_form.html', action='edit', genres=genres, book=book)

@app.route('/book/<int:book_id>/delete', methods=['POST'])
@login_required
def delete_book(book_id):
    if not check_rights([1]):
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    book = db.session.get(Book, book_id)
    if not book: return redirect(url_for('index'))
        
    try:
        if book.cover:
            same_file_covers = Cover.query.filter_by(filename=book.cover.filename).count()
            if same_file_covers == 1:
                path = os.path.join(app.config['UPLOAD_FOLDER'], book.cover.filename)
                if os.path.exists(path): os.remove(path)
        
        db.session.delete(book)
        db.session.commit()
        flash(f'Книга "{book.title}" успешно удалена.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при удалении книги: {str(e)}', 'danger')
        
    return redirect(url_for('index'))

@app.route('/book/<int:book_id>')
def view_book(book_id):
    book = db.session.get(Book, book_id)
    if not book: return redirect(url_for('index'))
         
    description_html = markdown.markdown(book.description)
    
    already_reviewed = False
    user_review = None
    if current_user.is_authenticated:
        user_review = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
        if user_review:
            already_reviewed = True
            user_review.text_html = markdown.markdown(user_review.text)

    for r in book.reviews:
        r.text_html = markdown.markdown(r.text)

    return render_template('book_view.html', book=book, desc_html=description_html, 
                           already_reviewed=already_reviewed, user_review=user_review)

@app.route('/book/<int:book_id>/review', methods=['GET', 'POST'])
@login_required
def add_review(book_id):
    book = db.session.get(Book, book_id)
    if not book: return redirect(url_for('index'))
         
    existing = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
    if existing: return redirect(url_for('view_book', book_id=book_id))

    if request.method == 'POST':
        try:
            rating = int(request.form.get('rating'))
            text = bleach.clean(request.form.get('text') or '')
            
            review = Review(book_id=book_id, user_id=current_user.id, rating=rating, text=text)
            db.session.add(review)
            db.session.commit()
            return redirect(url_for('view_book', book_id=book_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка сохранения отзыва: {str(e)}', 'danger')

    return render_template('review.html', book=book)

# --- ИНИЦИАЛИЗАЦИЯ И СТАРТ БАЗЫ ДАННЫХ ---
with app.app_context():
    db.create_all()
    
    if Role.query.count() == 0:
        admin_role = Role(id=1, name='Администратор', description='Полный доступ')
        moder_role = Role(id=2, name='Модератор', description='Модерация книг и рецензий')
        user_role = Role(id=3, name='Пользователь', description='Может оставлять отзывы')
        db.session.add_all([admin_role, moder_role, user_role])
        db.session.commit()

    if Genre.query.count() == 0:
        for g_name in ['Фантастика', 'Детектив', 'Роман', 'Ужасы', 'Наука']:
            db.session.add(Genre(name=g_name))
        db.session.commit()

    if User.query.count() == 0:
        pwd_hash = generate_password_hash('password123')
        admin = User(login='admin', password_hash=pwd_hash, last_name='Иванов', first_name='Иван', middle_name='Иванович', role_id=1)
        moderator = User(login='moderator', password_hash=pwd_hash, last_name='Petrov', first_name='Petr', middle_name='Petrovich', role_id=2)
        
        user1 = User(login='user', password_hash=pwd_hash, last_name='Сидоров', first_name='Сидор', middle_name='Сидорович', role_id=3)
        user2 = User(login='user2', password_hash=pwd_hash, last_name='Ковалева', first_name='Софья', middle_name='Сергеевна', role_id=3)
        
        db.session.add_all([admin, moderator, user1, user2])
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)