import os
import hashlib
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
import markdown
import bleach
from models import db, User, Book, Genre, Cover, Review

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-12345'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:password@localhost/electronic_library'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'covers')
app.config['PER_PAGE'] = 10

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    flash('Для выполнения данного действия необходимо пройти процедуру аутентификации', 'danger')
    return redirect(url_for('login'))

def check_rights(roles_allowed):
    if not current_user.is_authenticated:
        return False
    return current_user.role_id in roles_allowed

# --- МАРШРУТЫ ---

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    
    # Сбор данных для Варианта 3 (Поиск)
    title = request.args.get('title', '')
    author = request.args.get('author', '')
    genres_sel = request.args.getlist('genres')
    years_sel = request.args.getlist('years')
    pages_from = request.args.get('pages_from', '')
    pages_to = request.args.get('pages_to', '')

    query = Book.query

    if title: query = query.filter(Book.title.like(f"%{title}%"))
    if author: query = query.filter(Book.author.like(f"%{author}%"))
    if genres_sel: query = query.join(Book.genres).filter(Genre.id.in_(genres_sel))
    if years_sel: query = query.filter(Book.year.in_(years_sel))
    if pages_from: query = query.filter(Book.pages >= int(pages_from))
    if pages_to: query = query.filter(Book.pages <= int(pages_to))

    # Сортировка по году (сначала новые)
    query = query.order_by(Book.year.desc())
    
    pagination = query.paginate(page=page, per_page=app.config['PER_PAGE'], error_out=False)
    books = pagination.items

    # Вычисление среднего рейтинга и количества отзывов
    for book in books:
        revs = book.reviews
        book.avg_rating = round(sum(r.rating for r in revs) / len(revs), 2) if revs else 0
        book.rev_count = len(revs)

    # Данные для фильтров
    all_genres = Genre.query.all()
    all_years = [r[0] for r in db.session.query(Book.year).distinct().all()]

    return render_template('index.html', books=books, pagination=pagination, 
                           all_genres=all_genres, all_years=all_years)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login = request.form.get('login')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        
        user = User.query.filter_by(login=login).first()
        # В учебных целях хэш проверяется стандартно. Поменяйте под ваш хэш СУБД.
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
    if not check_rights([1]): # Только Администратор
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    genres = Genre.query.all()
    if request.method == 'POST':
        try:
            title = request.form.get('title')
            # Очистка HTML через Bleach
            description = bleach.clean(request.form.get('description'))
            year = request.form.get('year')
            publisher = request.form.get('publisher')
            author = request.form.get('author')
            pages = request.form.get('pages')
            genre_ids = request.form.getlist('genres')
            
            file = request.files.get('cover')
            if not file or file.filename == '':
                raise ValueError("Обложка обязательна")

            # Считаем MD5 хэш картинки
            file_bytes = file.read()
            md5_hash = hashlib.md5(file_bytes).hexdigest()
            file.seek(0)

            # Создаем книгу
            book = Book(title=title, description=description, year=year, publisher=publisher, author=author, pages=pages)
            for g_id in genre_ids:
                g = Genre.query.get(g_id)
                if g: book.genres.append(g)

            db.session.add(book)
            db.session.flush() # Получаем id книги до коммита

            # Работа с обложкой
            existing_cover = Cover.query.filter_by(md5_hash=md5_hash).first()
            if existing_cover:
                filename = existing_cover.filename
                mime_type = existing_cover.mime_type
            else:
                filename = secure_filename(f"{book.id}_{file.filename}")
                mime_type = file.mimetype
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            cover = Cover(filename=filename, mime_type=mime_type, md5_hash=md5_hash, book_id=book.id)
            db.session.add(cover)
            db.session.commit()
            
            return redirect(url_for('view_book', book_id=book.id))
        except Exception as e:
            db.session.rollback()
            flash('При сохранении данных возникла ошибка. Проверьте корректность введённых данных.', 'danger')
            
    return render_template('book_form.html', action='add', genres=genres, book=None)

@app.route('/book/<int:book_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_book(book_id):
    if not check_rights([1, 2]): # Админ и Модератор
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    book = Book.query.get_or_create(id=book_id)
    book = Book.query.get_or_404(book_id)
    genres = Genre.query.all()
    
    if request.method == 'POST':
        try:
            book.title = request.form.get('title')
            book.description = bleach.clean(request.form.get('description'))
            book.year = request.form.get('year')
            book.publisher = request.form.get('publisher')
            book.author = request.form.get('author')
            book.pages = request.form.get('pages')
            
            genre_ids = request.form.getlist('genres')
            book.genres = [Genre.query.get(g_id) for g_id in genre_ids if Genre.query.get(g_id)]
            
            db.session.commit()
            return redirect(url_for('view_book', book_id=book.id))
        except:
            db.session.rollback()
            flash('При сохранении данных возникла ошибка. Проверьте корректность введённых данных.', 'danger')

    return render_template('book_form.html', action='edit', genres=genres, book=book)

@app.route('/book/<int:book_id>/delete', methods=['POST'])
@login_required
def delete_book(book_id):
    if not check_rights([1]):
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    book = Book.query.get_or_404(book_id)
    try:
        # Пытаемся удалить файл обложки, если он больше нигде не используется
        if book.cover:
            same_file_covers = Cover.query.filter_by(filename=book.cover.filename).count()
            if same_file_covers == 1:
                path = os.path.join(app.config['UPLOAD_FOLDER'], book.cover.filename)
                if os.path.exists(path):
                    os.remove(path)
        
        db.session.delete(book)
        db.session.commit()
        flash(f'Книга "{book.title}" успешно удалена.', 'success')
    except:
        db.session.rollback()
        flash('Ошибка при удалении книги.', 'danger')
        
    return redirect(url_for('index'))

@app.route('/book/<int:book_id>')
def view_book(book_id):
    book = Book.query.get_or_404(book_id)
    # Превращаем описание Markdown в HTML
    description_html = markdown.markdown(book.description)
    
    # Проверка, писал ли уже этот пользователь рецензию
    already_reviewed = False
    user_review = None
    if current_user.is_authenticated:
        user_review = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
        if user_review:
            already_reviewed = True
            user_review.text_html = markdown.markdown(user_review.text)

    # Обработка Markdown для остальных рецензий
    for r in book.reviews:
        r.text_html = markdown.markdown(r.text)

    return render_template('book_view.html', book=book, desc_html=description_html, 
                           already_reviewed=already_reviewed, user_review=user_review)

@app.route('/book/<int:book_id>/review', methods=['GET', 'POST'])
@login_required
def add_review(book_id):
    book = Book.query.get_or_404(book_id)
    # Проверка дубликата
    existing = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
    if existing:
        return redirect(url_for('view_book', book_id=book_id))

    if request.method == 'POST':
        try:
            rating = int(request.form.get('rating'))
            text = bleach.clean(request.form.get('text'))
            
            review = Review(book_id=book_id, user_id=current_user.id, rating=rating, text=text)
            db.session.add(review)
            db.session.commit()
            return redirect(url_for('view_book', book_id=book_id))
        except:
            db.session.rollback()
            flash('Ошибка сохранения отзыва.', 'danger')

    return render_template('review.html', book=book)

if __name__ == '__main__':
    app.run(debug=True)