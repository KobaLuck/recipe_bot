from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, unique=True, nullable=False)
    username = db.Column(db.String(64))
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __str__(self):
        return f"@{self.username or self.telegram_id}"


class Tag(db.Model):
    __tablename__ = 'tag'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)

    def __str__(self):
        return self.name


class Ingredient(db.Model):
    __tablename__ = 'ingredient'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    measurement_unit = db.Column(db.String(50), nullable=False)

    __table_args__ = (
        db.UniqueConstraint(
            'name', 'measurement_unit', name='uq_ingredient_name_unit'
            ),
    )

    def __str__(self):
        return f"{self.name} ({self.measurement_unit})"


class RecipeIngredient(db.Model):
    __tablename__ = 'recipe_ingredient'

    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey(
        'recipe.id', ondelete='CASCADE'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey(
        'ingredient.id', ondelete='CASCADE'), nullable=False)
    amount = db.Column(db.String(50), nullable=False)

    recipe = db.relationship('Recipe', back_populates='recipe_ingredients')
    ingredient = db.relationship('Ingredient')

    __table_args__ = (
        db.UniqueConstraint(
            'recipe_id', 'ingredient_id', name='uq_recipe_ingredient'),
    )


class TagInRecipe(db.Model):
    __tablename__ = 'tag_in_recipe'

    id = db.Column(db.Integer, primary_key=True)
    tag_id = db.Column(
        db.Integer, db.ForeignKey('tag.id', ondelete='CASCADE'),
        nullable=False)
    recipe_id = db.Column(
        db.Integer, db.ForeignKey('recipe.id', ondelete='CASCADE'),
        nullable=False)

    tag = db.relationship('Tag')
    recipe = db.relationship('Recipe', back_populates='tag_links')

    __table_args__ = (
        db.UniqueConstraint('tag_id', 'recipe_id', name='uq_tag_in_recipe'),
    )


class Recipe(db.Model):
    __tablename__ = 'recipe'

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer,
                          db.ForeignKey('user.id', ondelete='SET NULL'))
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    image_path = db.Column(db.String(255))
    resource_url = db.Column(db.String(1000), default='https://avatars.mds.yandex.net/i?id=e8efae3f7d76a45b94612af5364cb135_l-4748118-images-thumbs&n=13')
    cooking_time = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    author = db.relationship('User', backref='recipes')
    recipe_ingredients = db.relationship('RecipeIngredient',
                                         back_populates='recipe',
                                         cascade='all, delete-orphan')
    tag_links = db.relationship('TagInRecipe',
                                back_populates='recipe',
                                cascade='all, delete-orphan')

    def __str__(self):
        return self.name


class Favorite(db.Model):
    __tablename__ = 'favorite'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer,
                        db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False)
    recipe_id = db.Column(db.Integer,
                          db.ForeignKey('recipe.id', ondelete='CASCADE'),
                          nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='favorites')
    recipe = db.relationship('Recipe', backref='favorites')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'recipe_id', name='uq_user_favorite'),
    )


class ShoppingCart(db.Model):
    __tablename__ = 'shopping_cart'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )
    recipe_id = db.Column(
        db.Integer,
        db.ForeignKey('recipe.id', ondelete='CASCADE'),
        nullable=False
    )
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Связи
    user = db.relationship('User', backref='shopping_carts')
    recipe = db.relationship('Recipe', backref='in_carts')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'recipe_id', name='uq_user_shopping_cart'),
    )

    def __str__(self):
        return f"{self.user} добавил «{self.recipe}» в корзину"
