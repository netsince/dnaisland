from datetime import datetime
import pytest
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.mysql import LONGTEXT

from app import create_app, db
from app.config import Config
from app.models import (
    Card,
    TeaPoll,
    TeaPollOption,
    TeaPollVote,
    TeaPost,
    TeaPostFavorite,
    TeaPostImage,
    TeaPostLike,
    TeaPostTopic,
    TeaTopic,
    User,
)


@compiles(LONGTEXT, "sqlite")
def compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_teahouse_models_create_and_relations(app):
    with app.app_context():
        # Create base user & card
        user = User(
            username="testuser",
            nickname="TestUser",
            email="test@example.com",
            password_hash="hash",
        )
        card = Card(id="card-123", author_id=1, name="Test Card")
        db.session.add_all([user, card])
        db.session.commit()

        # Create original post
        orig_post = TeaPost(user_id=user.id, content="Original Post")
        db.session.add(orig_post)
        db.session.commit()

        # Create quoted post with card reference
        quoted_post = TeaPost(
            user_id=user.id,
            content="Quoted Post",
            card_id=card.id,
            quote_post_id=orig_post.id,
        )
        db.session.add(quoted_post)
        db.session.commit()

        # Verify card & quote_post relationships
        assert quoted_post.card == card
        assert quoted_post.quote_post == orig_post
        assert orig_post in TeaPost.query.all()
        assert quoted_post in card.teaposts

        # Test TeaPostImage
        img1 = TeaPostImage(post_id=orig_post.id, image_data="base64_data_1", sort_order=1)
        img0 = TeaPostImage(post_id=orig_post.id, image_data="base64_data_0", sort_order=0)
        db.session.add_all([img1, img0])
        db.session.commit()

        db.session.refresh(orig_post)
        assert len(orig_post.images) == 2
        assert orig_post.images[0].sort_order == 0
        assert orig_post.images[1].sort_order == 1

        # Test TeaTopic & TeaPostTopic
        topic = TeaTopic(name="AI讨论", post_count=1)
        db.session.add(topic)
        db.session.commit()
        
        post_topic = TeaPostTopic(post_id=orig_post.id, topic_id=topic.id)
        db.session.add(post_topic)
        db.session.commit()

        assert TeaTopic.query.filter_by(name="AI讨论").first().id == topic.id
        assert TeaPostTopic.query.filter_by(post_id=orig_post.id, topic_id=topic.id).first() is not None

        # Test TeaPostFavorite
        fav = TeaPostFavorite(user_id=user.id, post_id=orig_post.id)
        db.session.add(fav)
        db.session.commit()
        assert TeaPostFavorite.query.filter_by(user_id=user.id, post_id=orig_post.id).first() is not None

        # Test TeaPoll & TeaPollOption & TeaPollVote
        poll = TeaPoll(post_id=orig_post.id, is_multiple=False)
        db.session.add(poll)
        db.session.commit()

        opt1 = TeaPollOption(poll_id=poll.id, option_text="Option A", vote_count=1)
        opt2 = TeaPollOption(poll_id=poll.id, option_text="Option B", vote_count=0)
        db.session.add_all([opt1, opt2])
        db.session.commit()

        db.session.refresh(orig_post)
        assert orig_post.poll == poll
        assert len(poll.options) == 2

        vote = TeaPollVote(poll_id=poll.id, option_id=opt1.id, user_id=user.id)
        db.session.add(vote)
        db.session.commit()

        assert TeaPollVote.query.filter_by(poll_id=poll.id, user_id=user.id).first().option_id == opt1.id
