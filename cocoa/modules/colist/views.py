# -*- coding: utf-8 -*-
from flask import Blueprint, request, render_template, \
    redirect, url_for
from flask.ext.login import current_user, login_required

from .models import Colist
from .forms import ColistNewForm

mod = Blueprint('colist', __name__)

@mod.route('/')
def home():

    return 'Colist index page.'


@mod.route('/new/', methods=['GET', 'POST'])
@login_required
def new():

    form = ColistNewForm(request.form)

    if form.validate_on_submit():
        colist = Colist(form.name.data, form.intro.data)
        colist.save()
        return redirect(url_for('colist.item', colist_id=colist.id))

    return render_template('colist/new.html', form=form)


@mod.route('/<int:colist_id>/')
def item(colist_id):

    colist = Colist.query.get(colist_id)
    return render_template('colist/item.html', colist=colist)


@mod.route('/<int:colist_id>/addbooks/', methods=['GET', 'POST'])
@login_required
def add_books(colist_id):

    colist = Colist.query.get(colist_id)

    if request.method == 'POST':
        book_ids = request.form.getlist('book_ids')
        colist.add_books(book_ids)

        return redirect(url_for('colist.item', colist_id=colist_id))

    return render_template('colist/add_books.html', colist=colist)
