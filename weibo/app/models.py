from datetime import datetime
import hashlib

from werkzeug.security import generate_password_hash, check_password_hash

from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from flask_login import UserMixin, AnonymousUserMixin
from flask import current_app, request
from . import db, login_manager
from . import blockchain

class Role(db.Model):
    """
    角色模型，三种角色类型 <普通用户 User> <协管员 Moderator> <管理员 Administrator>
    """
    __tablename__ = 'roles'     # 数据库表名

    id = db.Column(db.Integer, primary_key=True)                    # 角色 id
    name = db.Column(db.String, unique=True)                        # 角色名
    default = db.Column(db.Boolean, default=False, index=True)      # 角色属性是否默认，默认为`普通用户`
    permissions = db.Column(db.Integer)                             # 角色权限
    users = db.relationship('User', backref='role', lazy='dynamic') # 用户

    def to_blockchain(self):
        return {
            'id': self.id,
            'name': self.name,
            'is_default': self.default,
            'permissions': self.permissions,
        }

    @staticmethod
    def update_roles():
        """ 静态方法，为用户指定角色
        如若以后权限更改，则直接执行这个函数即可

        # 0x00 -> 匿名：未登录用户，在程序中只有阅读权限
        # 0x07 -> 用户：具有发布文章，发表评论和关注其他用户的权限，这是新用户默认的角色
        # 0x0f -> 协管员：增加审查不当评论的权限
        # 0xff -> 管理员：具有所有权限，包括修改其他用户所属角色的权限
        """
        roles = {
            'User': (Permission.FOLLOW |
                     Permission.COMMENT |
                     Permission.WRITE_ARTICLES, True),
            'Moderator': (Permission.FOLLOW |
                          Permission.COMMENT |
                          Permission.WRITE_ARTICLES |
                          Permission.MODERATE_COMMENTS, False),
            'Administrator': (0xff, False)
        }
        for r in roles:
            role = Role.query.filter_by(name=r).first()
            if role is None:
                role = Role(name=r)
            role.permissions = roles[r][0]
            role.default = roles[r][1]          # 默认为普通用户
            db.session.add(role)
            blockchain.blockChainPut("weibo.role." + str(role.id), str(role.to_blockchain()))            # 更新数据库
        db.session.commit()

    def __repr__(self):
        return '<Role {}>'.format(self.name)

class Follow(db.Model):
    """
    关注者/被关注者模型
    """
    __tablename = 'follows'     # 数据库表名

    # 用户关注的人 id
    follower_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    # 用户粉丝 id
    followed_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    # 关注时间
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_blockchain(self):
        return {
            'follower_id': self.follower_id,
            'followed_id': self.followed_id,
            'timestamp': self.timestamp,
        }

class User(UserMixin, db.Model):
    """
    用户模型
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        if not self.role:
            # 确定管理员
            if self.email == current_app.config['FLASK_ADMIN']:
                self.role = Role.query.filter_by(permissions=0xff).first()
            # 如果没有赋予角色则设置为`普通用户`
            else:
                self.role = Role.query.filter_by(default=True).first()
        # 根据用户邮箱确定头像哈希值
        if self.email is not None and self.avatar_hash is None:
            self.avatar_hash = hashlib.md5(self.email.encode('utf-8')).hexdigest()

    __tablename__ = 'users'     # 数据库表名

    id = db.Column(db.Integer, primary_key=True)                        # 用户 id
    email = db.Column(db.String(64), unique=True, index=True)           # 邮箱
    username = db.Column(db.String, unique=True, index=True)            # 用户名
    realname = db.Column(db.String(64))                                 # 真实姓名
    sex = db.Column(db.String, default='男')                            # 性别
    password_hash = db.Column(db.String(128))                           # 密码哈希值
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))          # 角色 id
    confirmed = db.Column(db.Boolean, default=False)                    # 是否保持登录
    location = db.Column(db.String(64))                                 # 地区
    about_me = db.Column(db.Text())                                     # 个人简介
    member_since = db.Column(db.DateTime, default=datetime.utcnow)      # 注册时间
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)         # 最新登录时间
    avatar_hash = db.Column(db.String(32))                              # 头像哈希值
    posts = db.relationship('Post', backref='author', lazy='dynamic')   # 微博<关系>

    # 用户关注的人<关系>
    followed = db.relationship('Follow',
                               foreign_keys=[Follow.follower_id],       # 在 Follow 模型中自引用
                               backref=db.backref('follower', lazy='joined'),   # join 立即加载所有相关对象
                               lazy='dynamic',
                               cascade='all, delete-orphan')            # 删除所有记录
    # 用户的粉丝<关系>
    followers = db.relationship('Follow',
                                foreign_keys=[Follow.followed_id],      # 在 Follow 模型中自引用
                                backref=db.backref('followed', lazy='joined'),  # join 立即加载所有相关对象
                                lazy='dynamic',
                                cascade='all, delete-orphan')           # 删除所有记录
    # 微博的评论<关系>
    comments = db.relationship('Comment',
                               backref='author',
                               lazy='dynamic')

    def set_follow(self, user):
        """ 设置关注用户

        :param user: 指定用户
        """
        if not self.is_following(user):
            f = Follow(follower=self, followed=user)
            db.session.add(f)
            blockchain.blockChainPut("weibo.follow." + str(f.follower_id)+"/"+str(f.followed_id), str(f.to_blockchain()))

    def set_unfollow(self, user):
        """ 设置取消关注用户

        :param user: 指定用户
        """
        f = self.followed.filter_by(followed_id=user.id).first()
        if f:
            # 从 follows 表中删除该关注关系
            db.session.delete(f)
            blockchain.blockChainDelete("weibo.follow." + str(f.follower_id)+"/"+str(f.followed_id))

    def is_following(self, user):
        """ 是否关注某用户

        :param user: 指定用户
        :return: 已关注返回 True，反之返回 False
        """
        if self.followed.filter_by(followed_id=user.id).first():
            return True
        return False

    def is_followed_by(self, user):
        """ 是否被某用户关注

        :param user: 指定用户
        :return: 已关注返回 True，反之返回 False
        """
        if self.followers.filter_by(follower_id=user.id).first():
            return True
        return False

    @property
    def followed_posts(self):
        """查询关注者微博列表，使用了联结操作，通过 user.id 链接 follow, post 两个数据表

        :return: 关注者微博列表
        """
        return Post.query.join(
            Follow, Follow.followed_id == Post.author_id).filter(
            Follow.follower_id == self.id)

    @property
    def password(self):
        """ 密码属性不可被访问
        """
        raise AttributeError('密码不可访问')

    @password.setter
    def password(self, password):
        """ 密码属性可写不可读

        :param password: 用户密码
        """
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        """ 密码验证

        :param password: 用户密码
        :return: 验证成功返回 True，反之返回 False
        """
        # return True
        return check_password_hash(self.password_hash, password)

    def generate_confirmation_token(self, expiration=3600):
        """ 生成用于确认身份的密令

        :param expiration: 密令有效时间，单位秒
        :return: 验证密令
        """
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'confirm': self.id})

    def confirm(self, token):
        """ 利用密令确认账户

        :param token: 验证密令
        :return:
        """
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)   # 解密密令
        except:
            return False
        if data.get('confirm') != self.id:
            return False
        self.confirmed = True       # 一旦成功确认 confirmed 属性设置为 True
        db.session.add(self)
        blockchain.blockChainPut("weibo.user." + str(self.id), str(self.to_blockchain()))
        return True

    def generate_auth_token(self, expiration):
        s = Serializer(current_app.config['SECRET_KEY'],
                       expires_in=expiration)
        return s.dumps({'id': self.id})

    @staticmethod
    def verify_auth_token(token):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return None
        return User.query.get(data['id'])

    def can(self, permissions):
        """ 权限判断
        利用逻辑运算符 &，如果经过 & 运算后仍为原先权限常量值，即确定用于拥有该权限。

        :param permissions: 指定权限
        :return: 验证成功返回 True，反之返回 False
        """
        return self.role is not None and \
               (self.role.permissions & permissions) == permissions

    @property
    def is_administrator(self):
        """ 判断是否为管理员

        :return: 是管理员返回 True，反之返回 False
        """
        return self.can(Permission.ADMINISTER)

    def update_last_seen(self):
        """ 更新用于最近一次登录时间
        """
        self.last_seen = datetime.utcnow()
        db.session.add(self)
        blockchain.blockChainPut("weibo.user." + str(self.id), str(self.to_blockchain()))

    def gravatar(self, size=100, default='identicon', rating='g'):
        """ 利用哈希值生成头像

        :param size: 头像大小
        :param default:
        :param rating:
        :return: 头像链接
        """
        if request.is_secure:       # https 类型
            url = 'https://secure.gravatar.com/avatar'
        else:                       # http 类型
            url = 'http://www.gravatar.com/avatar'
        _hash = self.avatar_hash or hashlib.md5(
            self.email.encode('utf-8')).hexdigest()
        return '{url}/{hash}?s={size}&d={default}&r={rating}'.format(
            url=url, hash=_hash, size=size, default=default, rating=rating)

    @staticmethod
    def generate_fake_users(count=100):
        from sqlalchemy.exc import  IntegrityError
        from random import seed
        import forgery_py

        seed()      # 随机数种子
        for i in range(count):
            _email = forgery_py.internet.email_address()
            u = User(
                email=_email,
                username=forgery_py.internet.user_name(),
                password=forgery_py.lorem_ipsum.word(),
                about_me=forgery_py.lorem_ipsum.sentence(),
                confirmed=True,
                avatar_hash=hashlib.md5(_email.encode('utf-8')).hexdigest(),
                member_since=forgery_py.date.date(True),
            )
            db.session.add(u)
            try:
                db.session.commit()
                blockchain.blockChainPut("weibo.user." + str(u.id), str(u.to_blockchain()))
            except IntegrityError:
                db.session.rollback()

    def to_json(self, posts):
        return {
            "id":self.id,
            "email":self.email,
            "comments":self.comments,
            'username': self.username,
            'memberSince': self.member_since,
            'lastSeen': self.last_seen,
            'postCount': self.posts.count(),
            'posts': posts
        }

    def to_blockchain(self):
        return {
            "id":self.id,
            "email":self.email,
            'username': self.username,
            'realname':self.username,
            'sex':self.sex,
            'password_hash':self.password_hash,
            'role_id':self.role_id,
            'confrim':self.confirmed,
            'location':self.location,
            'about_me':self.about_me,
            'member_since':str(self.member_since),
            'last_seen':str(self.last_seen),
            'avatar_hash':self.avatar_hash,
            'postCount': self.posts.count(),
            "comments_count":self.comments.count(),
            'followed':self.followed.count(),
            'followers':self.followers.count(),
        }

    def __repr__(self):
        return '<User {}>'.format(self.username)


class AnonymousUser(AnonymousUserMixin):
    """
    匿名用户（游客）模型
    """
    def can(self, permissions):
        """ 游客没有任何权限

        :param permissions: 指定权限
        :return: 无任何权限
        """
        return False

    @property
    def is_administrator(self):
        """ 判断是或否为管理员

        :return: 非管理员
        """
        return False


login_manager.anonymous_user = AnonymousUser    # 将未登录用户赋予游客模型

# login_manager.anonymous_user = User    # 将未登录用户赋予游客模型

@login_manager.user_loader
def load_user(user_id):
    """ Flask-Login 回调函数，用于指定标识符加载用户

    :param user_id: 用户 id
    :return: 查询到的用户对象
    """
    return User.query.get(int(user_id))


class Post(db.Model):
    """
    微博模型
    """
    __tablename__ = 'posts'     # 数据库表名

    id = db.Column(db.Integer, primary_key=True)                                # 微博 id
    body = db.Column(db.Text)                                                   # 微博内容
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)     # 发布时间
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))                # 作者 id
    comments = db.relationship('Comment', backref='post', lazy='dynamic')       # 评论

    @staticmethod
    def generate_fake_posts(count=100):
        from random import seed, randint
        import forgery_py

        seed()
        user_count = User.query.count()
        for i in range(count):
            u = User.query.offset(randint(0, user_count - 1)).first()
            p = Post(
                body=forgery_py.lorem_ipsum.sentences(randint(1, 3)),
                timestamp=forgery_py.date.date(True),
                author=u
            )
            db.session.add(p)
            db.session.commit()
            blockchain.blockChainPut("weibo.post." + str(p.id), str(p.to_blockchain()))

    def to_json(self):
        return {
            'posTime': self.timestamp,
            'post': self.body,
            'authorID': self.author_id,
            'comments':self.comments,
            'id':self.id
        }
    def to_blockchain(self):
        return {
            'id': self.id,
            'body': self.body,
            'timestamp': self.timestamp,
            'authorID': self.author_id,
            'comments':self.comments,
        }


class Comment(db.Model):
    """
    评论模型
    """
    __tablename__ = 'comments'      # 数据库表名

    id = db.Column(db.Integer, primary_key=True)                                # 评论 id
    body = db.Column(db.Text)                                                   # 评论内容
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)     # 评论时间
    disabled = db.Column(db.Boolean)                                            # 是否被屏蔽
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))                # 作者 id
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'))                  # 微博 id

    def to_json(self):
        return {
            'postTime': self.timestamp,
            'post': self.body,
            'postID': self.post_id,
            'authorID': self.author_id,
            'disabled':self.disabled
        }
    def to_blockchain(self):
        return {
            'postTime': self.timestamp,
            'post': self.body,
            'postID': self.post_id,
            'authorID': self.author_id,
            'disabled':self.disabled
        }


class Permission:
    """
    权限类，用于指定权限常量。当常量组合时可以构造不同身份权限。
    """
    FOLLOW = 0x01               # 关注其他用户
    COMMENT = 0x02              # 在他人撰写的文章中发表评论
    WRITE_ARTICLES = 0x04       # 写原创文章
    MODERATE_COMMENTS = 0x08    # 查处他人发表的不当评论
    ADMINISTER = 0x80           # 管理网站
