# -*- coding: utf-8 -*-
"""
    account models.
"""
import os
import re
import hashlib
from time import time
from datetime import datetime
from PIL import Image

from werkzeug import generate_password_hash, check_password_hash, \
     cached_property

from sqlalchemy.ext.associationproxy import association_proxy

from flask import current_app
from flask.ext.sqlalchemy import BaseQuery
from flask.ext.babel import gettext as _
from flask.ext.principal import UserNeed, RoleNeed, \
     identity_loaded

from cocoa.extensions import db, login_manager
from cocoa.permissions import moderator
from cocoa.helpers.sql import JSONEncodedDict
from cocoa.helpers.upload import mkdir
from .consts import Role, Gender, Status
from ..event.models import SignUpEvent
from ..tag.models import Tag, UserBookTags
from ..shelf.models import Shelf
from ..comment.models import BookShortReview
from ..vitality.models import UserVitality

class UserQuery(BaseQuery):

    def from_identity(self, identity):
        try:
            user = self.get(int(identity.name))
        except ValueError:
            user = None

        if user:
            identity.provides.update(user.principal_provides)

        identity.user = user

        return user

    def search(self, q):
        return self.filter(
                User.email.like(q) | User.penname.like(q)).\
               all()

    def get(self, id):
        return self.filter_by(id=id).\
               filter_by(status=Status.ACTIVATED.value).first()

    def get_inactive_user(self, id):
        return self.filter_by(id=id).\
               filter_by(status=Status.INACTIVE.value).first()


class User(db.Model):
    """用户表"""

    __tablename__ = 'user'

    query_class = UserQuery

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(100))
    username = db.Column(db.String(100), unique=True)  # 用户名
    penname = db.Column(db.String(100))                # 笔名
    intro = db.Column(db.Text)
    gender = db.Column(db.SmallInteger, default=Gender.SECRET.value)
    avatar = db.Column(db.String(100))
    thumbnail_box = db.Column(JSONEncodedDict(255))
    city_id = db.Column(db.String(20), db.ForeignKey('geo_city.city_id'))
    role = db.Column(db.SmallInteger, default=Role.MEMBER.value)
    status = db.Column(db.Integer, default=Status.INACTIVE.value)
    timestamp = db.Column(db.Integer, default=int(time()))

    city = db.relationship('City', backref='users')
    followings = association_proxy('u_followings', 'to_user')
    followers = association_proxy('u_followers', 'from_user')

    def __init__(self, email, password, city_id=None, username=None):
        self.email = email.lower()
        self.password = generate_password_hash(password)
        self.city_id = city_id
        self.username = username

        # TODO
        # check if email address is valid.

        if self.username is None:
            email_name = re.match(r'^([\d\w.-]+)@', self.email).group(1)
            if User.query.filter_by(username=email_name).first() is None:
                self.username = email_name
            else:
                sn = 2
                username = email_name + str(sn)
                while User.query.filter_by(username=username).first() \
                        is not None:
                    sn += 1
                    username = email_name + str(sn)
                self.username = username

    def __repr__(self):
        return '<User %r>' % self.email

    @cached_property
    def principal_provides(self):
        needs = [RoleNeed('member'), UserNeed(self.id)]

        if self.is_moderator:
            needs.append(RoleNeed('moderator'))

        if self.is_admin:
            needs.append(RoleNeed('admin'))

        return needs

    @property
    def is_moderator(self):
        return self.role >= Role.MODERATOR.value

    @property
    def is_admin(self):
        return self.role >= Role.ADMIN.value

    def save(self):
        """
            保存新注册用户（未激活）

        """

        u = User.query.filter_by(email=self.email).first()
        if u is None:
            db.session.add(self)
            db.session.commit()
        else:
            raise ValueError(_('This email has been signed up.'))

    def account_confirm(self, hashstr):
        """
            帐号确认

            1.初始化用户书架
            2.初始化“用户活跃度”表
            3.初始化默认相册
            4.写入“注册”事件
        """

        from ..photoalbum.models import Album

        confirm = SignupConfirm.query.get(self.id)
        if confirm is None or confirm.hashstr != hashstr:
            return False;
        else:
            confirm.accepted = True
            self.status = Status.ACTIVATED.value
            self.shelf = Shelf()
            self.vitality = UserVitality()
            self.albums.append(Album('default', default=True))
            db.session.commit()

            e = SignUpEvent(self.id)
            e.save()
            return True
    
    def update(self, penname, intro, gender, city_id):
        self.penname = penname
        self.intro = intro
        self.gender = gender
        self.city_id = city_id

        db.session.commit()

    def check_password(self, password):
        if self.password is None:
            return False
        return check_password_hash(self.password, password)

    def update_password(self, old, new):
        if not self.check_password(old):
            raise ValueError(_('Wrong previous password.'))
        else:
            self.password = generate_password_hash(new)
            db.session.commit()

    # Deprecated.
    # use (property) self.display_name instead.
    def get_display_name(self):
        if self.penname is not None:
            return self.penname
        else:
            return self.username

    @cached_property
    def display_name(self):
        if self.penname is not None:
            return self.penname
        else:
            return self.username

    @staticmethod
    def authenticate(email, password):
        u = User.query.filter_by(email=email).first()
        if u is None or u.status != Status.ACTIVATED.value or \
                not check_password_hash(u.password, password):
            return None, False
        else:
            return u, True

    # functions that are required by Flask-Login
    def is_authenticated(self):
        return True

    def is_active(self):
        return self.status == Status.ACTIVATED.value

    def is_anonymous(self):
        return False

    def get_id(self):
        return unicode(self.id)
    # end

    @cached_property
    def location(self):
        return self.get_location()['text']

    def get_location(self):
        location = {
            'city_id': None,
            'province_id': None,
            'text': '',
        }

        if self.city is None:
            return location

        location['city_id'] = self.city.city_id
        location['province_id'] = self.city.province.province_id
        province_name = self.city.province.get_short_name()
        city_name = self.city.name
        location['text'] = province_name + ' ' + city_name

        return location

    def set_thumbnail_box(self, box):
        self.thumbnail_box = dict(zip(('x1', 'y1', 'x2', 'y2'), box))

    @cached_property
    def thumbnail_path(self):
        return current_app.config['AVATAR_STATIC_PATH'] + self.thumbnail

    @cached_property
    def thumbnail(self):
        if self.avatar is None:
            return 'default_t.jpg'
        else:
            slices = self.avatar.split('.')
            return slices[0] + '_t.' + slices[1]

    # Deprecated.
    # use (property) self.thumbnail instead.
    def get_thumbnail(self):
        if self.avatar is None:
            return 'default_t.jpg'
        else:
            slices = self.avatar.split('.')
            return slices[0] + '_t.' + slices[1]
    
    @staticmethod
    def get_by_email(email):
        return User.query.filter_by(email=email).first()

    def add_booktags(self, book, tag_names):
        if tag_names is not None:
            for name in tag_names:
                book_tag = UserBookTags.query.outerjoin(Tag). \
                           filter(Tag.name==name,
                           UserBookTags.book_id==book.id,
                           UserBookTags.user_id==self.id).first()

                if book_tag is not None: return

                tag = book.add_tag(name)
                self.user_tags.append(UserBookTags(tag, book, self))

            db.session.commit()

    def publish_short_review(self, book, short_review):
        s_review = BookShortReview(short_review, book, self)
        s_review.save()

    def get_gender(self):
        return Gender.from_int(self.gender).text

    def unread_mail_count(self):
        from ..mail.models import MailInbox
        return MailInbox.query.filter_by(user=self).\
                filter_by(unread=True).count()

    def recent_posts(self, num=10):
        from ..blog.models import Post
        return Post.query.filter_by(author=self).\
                order_by(Post.timestamp.desc()).limit(num)

    def get_book_short_review(self, book_id):
        from ..comment.models import BookShortReview
        return BookShortReview.query.filter_by(book_id=book_id).\
                filter_by(user_id=self.id).first()


class SignupConfirmQuery(BaseQuery):

    expiration = 1    # one day

    def get(self, user_id):
        confirm = self.filter_by(user_id=user_id).\
                  order_by(SignupConfirm.id.desc()).first()
        if confirm is None:
            return None

        diff = datetime.now() - datetime.fromtimestamp(confirm.timestamp)
        if diff.days < 1:
            return confirm
        else:
            return None


class SignupConfirm(db.Model):
    """注册验证"""

    __tablename__ = 'signup_confirm'

    query_class = SignupConfirmQuery

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    hashstr = db.Column(db.String(255))
    timestamp = db.Column(db.Integer, default=int(time()))
    accepted = db.Column(db.Boolean, default=False)

    user = db.relationship('User')

    def __init__(self, user=None):
        self.user = user

        m = hashlib.md5()
        m.update(str(int(time())))
        self.hashstr = m.hexdigest()

    def save(self):
        db.session.add(self)
        db.session.commit()


@login_manager.user_loader
def load_user(user_id):

    return User.query.get(user_id)
