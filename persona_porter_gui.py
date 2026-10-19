#!/usr/bin/env python3
"""Persona Porter: native Tk interface for bidirectional PS2 model/UI swaps."""
import base64
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from persona_porter import FormatError
from porting import GameImage, Options, QueueEntry, batch_plan, build_batch, validate_queue
from swap_copy import BuildCancelled
from gameplay_editor import GameplayDialog, CAMPAIGN_LABELS


class PersonaList(ttk.LabelFrame):
    def __init__(self, parent, title, changed):
        super().__init__(parent, text=title, padding=10)
        self.changed = changed
        self.game = None
        self.filter_limits = None
        self.photo = None
        self.search = tk.StringVar()
        self.search_entry = ttk.Entry(self, textvariable=self.search)
        self.search_entry.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        self.search.trace_add('write', lambda *_: self.refresh())
        self.tree = ttk.Treeview(self, columns=('id', 'name', 'battle', 'facility'), show='headings', height=7,
                                 selectmode='browse')
        for key, label, width in [('id', 'ID', 42), ('name', 'Persona', 150),
                                   ('battle', 'Battle KiB', 76), ('facility', 'Menu KiB', 76)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=width, stretch=key == 'name')
        self.tree.grid(row=1, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _: self.changed())
        self.disabled = False
        self.tree.bind('<Button-1>', lambda _: 'break' if self.disabled else None)
        self.tree.bind('<KeyPress>', lambda _: 'break' if self.disabled else None)
        self.preview_label = ttk.Label(self, text='Select a Persona', anchor='center')
        self.preview_label.grid(row=2, column=0, columnspan=2, sticky='nsew', pady=(8, 0))
        self.caption = ttk.Label(self, text='', anchor='center')
        self.caption.grid(row=3, column=0, columnspan=2, sticky='ew')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

    def selected(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def set_game(self, game, selected=None):
        self.game = game
        self.search.set('')
        self.refresh(selected)

    def refresh(self, selected=None):
        if not hasattr(self, 'tree'):
            return
        selected = self.selected() if selected is None else selected
        self.tree.delete(*self.tree.get_children())
        if not self.game:
            return
        query = self.search.get().casefold().strip()
        for p in self.game.catalog:
            if query and query not in p['name'].casefold() and query not in f'{p["id"]:03x}':
                continue
            if self.filter_limits and (p['battle_size'] < self.filter_limits[0] or p['facility_size'] < self.filter_limits[1]):
                continue
            self.tree.insert('', 'end', iid=str(p['id']), values=(f'{p["id"]:03X}', p['name'],
                             f'{p["battle_size"] / 1024:.1f}', f'{p["facility_size"] / 1024:.1f}'))
        if selected is not None and self.tree.exists(str(selected)):
            self.tree.selection_set(str(selected))
            self.tree.see(str(selected))
            def show_selection():
                if self.tree.exists(str(selected)) and self.selected() == selected:
                    self.tree.see(str(selected))
            self.after(100, show_selection)
        elif not self.tree.selection():
            self.tree.yview_moveto(0)

    def preview(self):
        index = self.selected()
        if self.game is None or index is None:
            self.photo = None
            self.preview_label.configure(image='', text='Select a Persona')
            self.caption.configure(text='')
            return
        try:
            full = tk.PhotoImage(data=base64.b64encode(self.game.preview(index)))
            self.photo = full.subsample(4, 4)
            self.preview_label.configure(image=self.photo, text='')
        except (FormatError, OSError, tk.TclError) as exc:
            self.photo = None
            self.preview_label.configure(image='', text=f'Preview unavailable: {exc}')
        self.caption.configure(text=f'{self.game.name(index)} · {index:03X}')


class App(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=16)
        self.root = root
        self.pack(fill='both', expand=True)
        root.title('Persona Porter — P3 FES ↔ P4')
        root.geometry('1150x900')
        root.minsize(1000, 830)
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.plan_key = None
        self.images = None
        self.swap_queue = []
        self.queue_context = None
        self.draft_gameplay = ()
        self.gameplay_key = None
        self.controls = []
        self.source_path = tk.StringVar()
        self.target_path = tk.StringVar()
        self.status = tk.StringVar(value='Choose clean original game ISOs, then load the Persona lists.')
        self.progress = tk.DoubleVar(value=0)
        self.flags = {key: tk.BooleanVar(value=True) for key in ('name', 'portrait', 'card', 'lore')}
        self.custom = tk.BooleanVar(value=False)
        self.fit = tk.BooleanVar(value=False)
        self.expand = tk.BooleanVar(value=True)
        self.capacity = tk.StringVar(value='Select source and target Personas to see lore capacity.')
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(self, text='Persona Porter', font=('TkDefaultFont', 22, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(self, text='PS2 · P3 FES ↔ P4 · v0.10', anchor='e').grid(row=0, column=1, sticky='e')
        paths = ttk.Frame(self)
        paths.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(12, 12))
        paths.columnconfigure(1, weight=1)
        for row, (label, var) in enumerate((('Source ISO', self.source_path), ('Target ISO', self.target_path))):
            ttk.Label(paths, text=label, width=11).grid(row=row, column=0, sticky='w', pady=3)
            entry = ttk.Entry(paths, textvariable=var)
            entry.grid(row=row, column=1, sticky='ew', padx=8)
            browse = ttk.Button(paths, text='Browse…', command=lambda v=var: self.browse(v))
            browse.grid(row=row, column=2)
            self.controls.extend((entry, browse))
        self.load_button = ttk.Button(paths, text='Load games', command=self.load_games)
        self.load_button.grid(row=0, column=3, padx=(10, 0))
        self.reverse_button = ttk.Button(paths, text='Reverse direction', command=self.reverse, state='disabled')
        self.reverse_button.grid(row=1, column=3, padx=(10, 0))
        self.controls.extend((self.load_button, self.reverse_button))
        self.source_list = PersonaList(self, 'Source Persona — import this model and identity', self.source_changed)
        self.source_list.grid(row=2, column=0, sticky='nsew', padx=(0, 6))
        self.target_list = PersonaList(self, 'Target slot — replace this Persona', self.target_changed)
        self.target_list.grid(row=2, column=1, sticky='nsew', padx=(6, 0))
        fit_button = ttk.Checkbutton(self, text='Show only target slots large enough for both models', variable=self.fit,
                                    command=self.filter_targets)
        fit_button.grid(row=3, column=1, sticky='w', pady=(4, 8))
        self.controls.append(fit_button)
        expand_button = ttk.Checkbutton(self, text='Allow larger models (experimental archive expansion)',
                                        variable=self.expand, command=self.invalidate)
        expand_button.grid(row=3, column=0, sticky='w', pady=(4, 8))
        self.controls.append(expand_button)
        self.lore_panel = ttk.LabelFrame(self, text='UI identity', padding=10)
        self.lore_panel.grid(row=4, column=0, sticky='nsew', padx=(0, 6), pady=4)
        self.lore_panel.columnconfigure(0, weight=1)
        switches = ttk.Frame(self.lore_panel)
        switches.grid(row=0, column=0, sticky='ew')
        for col, (key, label) in enumerate((('name', 'Name'), ('portrait', 'Portrait'), ('card', 'Card'), ('lore', 'Lore'))):
            control = ttk.Checkbutton(switches, text=label, variable=self.flags[key], command=self.invalidate)
            control.grid(row=0, column=col, padx=(0, 12))
            self.controls.append(control)
        ttk.Label(self.lore_panel, textvariable=self.capacity, wraplength=490).grid(row=1, column=0, sticky='w', pady=6)
        self.lore_text = ScrolledText(self.lore_panel, height=7, width=45, wrap='word', undo=True)
        self.lore_text.grid(row=2, column=0, sticky='nsew')
        self.lore_text.bind('<<Modified>>', self.lore_modified)
        custom_button = ttk.Checkbutton(self.lore_panel, text='Use this edited lore (ASCII only)', variable=self.custom,
                                       command=self.invalidate)
        custom_button.grid(row=3, column=0, sticky='w', pady=(6, 0))
        self.controls.append(custom_button)
        gameplay_bar = ttk.Frame(self.lore_panel)
        gameplay_bar.grid(row=4, column=0, sticky='ew', pady=(8, 0))
        gameplay_bar.columnconfigure(1, weight=1)
        self.gameplay_button = ttk.Button(gameplay_bar, text='Edit gameplay…', command=self.edit_gameplay)
        self.gameplay_button.grid(row=0, column=0, sticky='w')
        self.gameplay_status = tk.StringVar(value='Keep target gameplay')
        ttk.Label(gameplay_bar, textvariable=self.gameplay_status, wraplength=270).grid(row=0, column=1, sticky='w', padx=8)
        self.controls.append(self.gameplay_button)
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=4, column=1, sticky='nsew', padx=(6, 0), pady=4)
        self.queue_panel = ttk.Frame(self.notebook, padding=8)
        self.queue_panel.columnconfigure(0, weight=1)
        self.queue_panel.rowconfigure(1, weight=1)
        self.notebook.add(self.queue_panel, text='Swap queue (0)')
        ttk.Label(self.queue_panel, text='Each entry keeps its own UI choices and edited lore.',
                  wraplength=480).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))
        self.queue_tree = ttk.Treeview(self.queue_panel, columns=('source', 'target', 'ui'),
                                       show='headings', height=5, selectmode='browse')
        for key, title, width in (('source', 'Source', 145), ('target', 'Target slot', 145), ('ui', 'UI choices', 160)):
            self.queue_tree.heading(key, text=title)
            self.queue_tree.column(key, width=width, minwidth=70)
        self.queue_tree.grid(row=1, column=0, sticky='nsew')
        queue_scroll = ttk.Scrollbar(self.queue_panel, orient='vertical', command=self.queue_tree.yview)
        queue_scroll.grid(row=1, column=1, sticky='ns')
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        queue_actions = ttk.Frame(self.queue_panel)
        queue_actions.grid(row=2, column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.remove_button = ttk.Button(queue_actions, text='Remove selected', command=self.remove_queued)
        self.remove_button.pack(side='left')
        self.clear_button = ttk.Button(queue_actions, text='Clear queue', command=self.clear_queue)
        self.clear_button.pack(side='left', padx=8)
        self.controls.extend((self.remove_button, self.clear_button))
        self.report = ScrolledText(self.notebook, height=14, width=50, wrap='word', state='disabled')
        self.notebook.add(self.report, text='Checks / build report')
        ttk.Label(self, text='Transfers models, selected UI and saved gameplay edits. Arcana, inheritance and fusion rules stay unchanged.',
                  wraplength=1080).grid(row=5, column=0, columnspan=2, sticky='w', pady=(8, 4))
        actions = ttk.Frame(self)
        actions.grid(row=6, column=0, columnspan=2, sticky='ew', pady=(4, 6))
        self.add_button = ttk.Button(actions, text='Add selected swap', command=self.add_queued, state='disabled')
        self.add_button.pack(side='left', padx=(0, 8))
        self.check_button = ttk.Button(actions, text='Check queue', command=self.analyze, state='disabled')
        self.check_button.pack(side='left')
        self.build_button = ttk.Button(actions, text='Build new ISO…', command=self.build, state='disabled')
        self.build_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(actions, text='Cancel build', command=self.cancel_event.set, state='disabled')
        self.cancel_button.pack(side='right')
        self.controls.extend((self.check_button, self.add_button, self.build_button))
        ttk.Progressbar(self, variable=self.progress, maximum=100).grid(row=7, column=0, columnspan=2, sticky='ew')
        ttk.Label(self, textvariable=self.status, wraplength=1080).grid(row=8, column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.source_path.trace_add('write', lambda *_: self.invalidate(force=True))
        self.target_path.trace_add('write', lambda *_: self.invalidate(force=True))
        self.default_paths()
        self.set_report('Choose the source and target games, then click Load games.\n\n'
                        'Select a pair and click Add selected swap. Repeat for each target, then Check queue and Build new ISO. '
                        'Original ISOs are opened read-only. '
                        'Each build creates a new ISO and a verification report.\n\n'
                        'Only the two NTSC-U editions used in this project are supported. '
                        'The P4 Mokoi → P3 FES Orpheus recipe is confirmed in game. Other combinations need testing.')
        root.protocol('WM_DELETE_WINDOW', self.close)
        modifier = 'Command' if root.tk.call('tk', 'windowingsystem') == 'aqua' else 'Control'
        for key, action in (('l', self.load_games), ('r', self.reverse),
                            ('Return', self.analyze), ('b', self.build), ('a', self.add_queued), ('e', self.edit_gameplay)):
            def shortcut(event, callback=action):
                if not self.busy:
                    callback()
                return 'break'
            root.bind(f'<{modifier}-{key}>', shortcut)
        root.after(100, self.poll)

    def default_paths(self):
        folder = Path.home() / 'Documents/PCSX2/games'
        for var, name in ((self.source_path, 'Shin Megami Tensei - Persona 4.iso'),
                          (self.target_path, 'Shin Megami Tensei - Persona 3 FES.iso')):
            path = folder / name
            if path.is_file():
                var.set(str(path))

    def browse(self, var):
        path = filedialog.askopenfilename(title='Choose a clean original PS2 ISO', filetypes=[('ISO images', '*.iso'), ('All files', '*')])
        if path:
            var.set(path)
            self.invalidate()

    def set_report(self, text):
        self.report.configure(state='normal')
        self.report.delete('1.0', 'end')
        self.report.insert('1.0', text)
        self.report.configure(state='disabled')
        self.notebook.select(self.report)

    def invalidate(self, force=False):
        if self.swap_queue and not force:
            return  # Draft edits do not change the saved queue entries.
        was_valid = self.plan_key is not None
        self.plan_key = None
        self.build_button.configure(state='disabled')
        if was_valid:
            self.set_report('The queue or ISO paths changed. Check the queue again before building.')

    def lore_modified(self, _):
        if self.lore_text.edit_modified():
            self.lore_text.edit_modified(False)
            self.invalidate()

    def set_busy(self, value, cancellable=False):
        self.busy = value
        for panel in (self.source_list, self.target_list):
            panel.disabled = value
            panel.search_entry.configure(state='disabled' if value else 'normal')
            panel.tree.state(['disabled'] if value else ['!disabled'])
        for control in self.controls:
            control.configure(state='disabled' if value else 'normal')
        self.lore_text.configure(state='disabled' if value else 'normal')
        self.cancel_button.configure(state='normal' if value and cancellable else 'disabled')
        if not value:
            self.reverse_button.configure(state='normal' if self.images else 'disabled')
            self.check_button.configure(state='normal' if self.swap_queue else 'disabled')
            self.add_button.configure(state='normal' if self.images else 'disabled')
            self.build_button.configure(state='normal' if self.plan_key is not None else 'disabled')

    def run(self, function, done, cancellable=False):
        self.cancel_event.clear()
        self.set_busy(True, cancellable)
        def task():
            try:
                self.events.put(('done', done, function()))
            except Exception as exc:
                self.events.put(('error', exc))
        threading.Thread(target=task, daemon=True).start()

    def poll(self):
        try:
            while True:
                item = self.events.get_nowait()
                if item[0] == 'progress':
                    self.status.set(item[1])
                    self.progress.set(item[2])
                elif item[0] == 'done':
                    self.set_busy(False)
                    try:
                        item[1](item[2])
                    except Exception as exc:
                        self.invalidate()
                        self.status.set(str(exc))
                        self.set_report(str(exc))
                        messagebox.showerror('Could not update the window', str(exc))
                else:
                    self.set_busy(False)
                    self.invalidate()
                    self.progress.set(0)
                    self.status.set(str(item[1]))
                    self.set_report(str(item[1]))
                    if not isinstance(item[1], BuildCancelled):
                        messagebox.showerror('Could not complete the operation', str(item[1]))
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def load_games(self):
        source, target = self.source_path.get(), self.target_path.get()
        if not source or not target:
            messagebox.showinfo('Choose both ISOs', 'Choose the source ISO and the target ISO first.')
            return
        if self.swap_queue and (str(Path(source).resolve()), str(Path(target).resolve())) != self.queue_context:
            messagebox.showinfo('Queue uses different ISOs', 'Clear the queue before loading different game ISOs.')
            return
        self.invalidate(force=True)
        self.status.set('Reading the game archives and Persona names…')
        def task():
            a, b = GameImage(source), GameImage(target)
            if a.serial == b.serial:
                raise FormatError('Choose one P3 FES ISO and one P4 ISO.')
            return a, b
        def done(images):
            self.images = images
            self.reverse_button.configure(state='normal')
            self.add_button.configure(state='normal')
            self.check_button.configure(state='normal' if self.swap_queue else 'disabled')
            self.source_list.filter_limits = None
            self.target_list.filter_limits = None
            source_id = 78 if images[0].game['key'] == 'p4' else 1
            self.source_list.set_game(images[0], source_id)
            self.target_list.set_game(images[1], 1)
            self.source_changed()
            self.target_changed()
            self.status.set(f'{images[0].title} → {images[1].title} · Choose Personas and add swaps to the queue.')
        self.run(task, done)

    def reverse(self):
        if not self.images or self.busy:
            return
        if self.swap_queue:
            messagebox.showinfo('Queue has a direction', 'Clear the queue before reversing the game direction.')
            return
        source_id, target_id = self.source_list.selected(), self.target_list.selected()
        a, b = self.images
        self.images = b, a
        self.source_path.set(str(b.path))
        self.target_path.set(str(a.path))
        self.source_list.filter_limits = None
        self.target_list.filter_limits = None
        self.source_list.set_game(b, target_id)
        self.target_list.set_game(a, source_id)
        self.custom.set(False)
        self.source_changed()
        if self.target_list.selected() is None:
            available = self.target_list.tree.get_children()
            if available:
                self.target_list.tree.selection_set(available[0])
                self.target_list.tree.see(available[0])
        self.target_changed()
        self.status.set(f'{b.title} → {a.title}')

    def filter_targets(self):
        if not self.images:
            return
        selected = self.source_list.selected()
        if self.fit.get() and selected is not None:
            p = next(p for p in self.images[0].catalog if p['id'] == selected)
            self.target_list.filter_limits = p['battle_size'], p['facility_size']
        else:
            self.target_list.filter_limits = None
        self.target_list.refresh()
        self.invalidate()

    def source_changed(self):
        if self.busy:
            return
        self.invalidate()
        self.source_list.preview()
        self.custom.set(False)
        self.lore_text.delete('1.0', 'end')
        i = self.source_list.selected()
        if self.images and i is not None:
            try:
                self.lore_text.insert('1.0', self.images[0].lore(i)[0])
            except FormatError as exc:
                self.lore_text.insert('1.0', f'Lore unavailable: {exc}')
        self.filter_targets()
        self.update_capacity()

    def target_changed(self):
        if self.busy:
            return
        self.invalidate()
        self.target_list.preview()
        self.sync_gameplay()
        self.update_capacity()

    def sync_gameplay(self):
        index = self.target_list.selected()
        key = (str(self.images[1].path), index) if self.images and index is not None else None
        if key != self.gameplay_key:
            self.gameplay_key = key
            self.draft_gameplay = ()
            self.gameplay_status.set('Keep target gameplay')

    def edit_gameplay(self):
        if self.busy:
            return
        try:
            _, target, _, index, _ = self.selection()
            self.sync_gameplay()
            dialog = GameplayDialog(self.root, target, index, self.draft_gameplay)
            if dialog.result is not None:
                self.draft_gameplay = dialog.result
                campaigns = ', '.join(CAMPAIGN_LABELS[edit.campaign] for edit in self.draft_gameplay)
                self.gameplay_status.set('Edited: ' + campaigns if campaigns else 'Keep target gameplay')
                self.invalidate()
                self.status.set('Gameplay choices saved for this draft. Add the selected swap to save them in the queue.')
        except (FormatError, OSError) as exc:
            messagebox.showerror('Gameplay editor unavailable', str(exc))

    def update_capacity(self):
        i = self.target_list.selected()
        if i is None:
            self.capacity.set('Select a target slot to see its lore capacity.')
            return
        if self.images and i is not None:
            try:
                _, capacity = self.images[1].lore(i)
                self.capacity.set(f'Source lore below. Target can hold {capacity - 1} ASCII characters including line breaks.')
            except FormatError as exc:
                self.capacity.set(f'Target lore unavailable: {exc}. Turn off lore transfer for this slot.')

    def options(self):
        return Options(**{k: v.get() for k, v in self.flags.items()},
                       custom_lore=self.lore_text.get('1.0', 'end-1c') if self.custom.get() else None,
                       expand_models=self.expand.get())

    def selection(self):
        if not self.images:
            raise FormatError('Load both games first.')
        source, target = self.images
        if Path(self.source_path.get()).resolve() != source.path or Path(self.target_path.get()).resolve() != target.path:
            raise FormatError('ISO paths changed. Click Load games again.')
        si, ti = self.source_list.selected(), self.target_list.selected()
        if si is None or ti is None:
            raise FormatError('Select a Persona in each list. Turn off the fit filter to see smaller target slots.')
        return source, target, si, ti, self.options()

    def queue_selection(self):
        if not self.images:
            raise FormatError('Load both games first.')
        source, target = self.images
        context = (str(Path(self.source_path.get()).resolve()), str(Path(self.target_path.get()).resolve()))
        if context != (str(source.path), str(target.path)):
            raise FormatError('ISO paths changed. Restore the queued ISO paths or clear the queue and load games again.')
        if self.queue_context is not None and context != self.queue_context:
            raise FormatError('This queue belongs to different ISOs. Clear it before switching games.')
        return source, target, validate_queue(self.swap_queue)

    def key(self):
        a, b, entries = self.queue_selection()
        return str(a.path), str(b.path), entries

    def refresh_queue(self):
        self.queue_tree.delete(*self.queue_tree.get_children())
        if self.images:
            source, target = self.images
            for i, entry in enumerate(self.swap_queue):
                ui = ', '.join(name for name in ('name', 'portrait', 'card', 'lore') if getattr(entry.options, name)) or 'Models only'
                if entry.options.lore and entry.options.custom_lore is not None:
                    ui += ' (edited)'
                if entry.gameplay:
                    ui += ' + gameplay'
                self.queue_tree.insert('', 'end', iid=str(i), values=(
                    f'{source.name(entry.source_id)} [{entry.source_id:03X}]',
                    f'{target.name(entry.target_id)} [{entry.target_id:03X}]', ui))
        self.notebook.tab(self.queue_panel, text=f'Swap queue ({len(self.swap_queue)})')
        self.check_button.configure(state='normal' if self.swap_queue else 'disabled')
        self.invalidate(force=True)
        self.notebook.select(self.queue_panel)

    def add_queued(self):
        if self.busy:
            return
        try:
            source, target, si, ti, options = self.selection()
            context = str(source.path), str(target.path)
            if self.queue_context is not None and self.queue_context != context:
                raise FormatError('Clear the queue before changing game ISOs.')
            self.sync_gameplay()
            entry = QueueEntry(si, ti, options, self.draft_gameplay)
            proposed = validate_queue([*self.swap_queue, entry])
        except (FormatError, OSError) as exc:
            messagebox.showinfo('Could not add swap', str(exc))
            return
        self.status.set('Checking the new entry together with the queue…')
        def done(plan):
            self.swap_queue = list(proposed)
            self.queue_context = context
            self.refresh_queue()
            row = str(len(self.swap_queue) - 1)
            self.queue_tree.selection_set(row)
            self.queue_tree.see(row)
            self.status.set(f'{len(self.swap_queue)} swap(s) queued. Add another pair or check the queue before building.')
        self.run(lambda: batch_plan(source, target, proposed), done)

    def remove_queued(self):
        if self.busy:
            return
        selected = self.queue_tree.selection()
        if selected:
            del self.swap_queue[int(selected[0])]
            if not self.swap_queue:
                self.queue_context = None
            self.refresh_queue()
            self.status.set(f'{len(self.swap_queue)} swap(s) queued.')

    def clear_queue(self):
        if self.busy:
            return
        self.swap_queue.clear()
        self.queue_context = None
        self.refresh_queue()
        self.status.set('Queue cleared. Select a pair and click Add selected swap.')

    def analyze(self):
        try:
            source, target, entries = self.queue_selection()
            key = self.key()
        except (FormatError, OSError) as exc:
            messagebox.showinfo('Check the queue', str(exc))
            return
        self.invalidate(force=True)
        self.status.set('Checking all queued models, UI files and lore copies…')
        def done(plan):
            if self.key() != key:
                return
            self.plan_key = key
            self.build_button.configure(state='normal')
            lines = [f'{source.title} → {target.title}',
                     f'{plan["swap_count"]} swap(s) ready to build into one ISO.', '']
            for swap in plan['swaps']:
                a, b = swap['source_persona'], swap['target_slot']
                lines += [f'{a["name"]} [{a["id"]:03X}] → {b["original_name"]} [{b["id"]:03X}]',
                          'UI: ' + (', '.join(k for k, v in swap['transfer'].items() if v) or 'Models only')]
                for gameplay in swap['gameplay']:
                    edit = gameplay['edits']
                    fields = [label for key, label in (('stats', 'base stats'), ('affinities', 'resistances'), ('skills', 'skill list')) if edit[key] is not None]
                    lines += [f'Gameplay ({CAMPAIGN_LABELS[gameplay["campaign"]]}): ' + ', '.join(fields)]
            lines += ['',
                      (f'Experimental archive expansion: output grows by '
                       f'{(plan["output_iso_size"] - plan["target_iso_size"]) / 1024**2:.1f} MiB.'
                       if plan.get('expanded_archives') else 'All models fit their original file spaces.'),
                      f'{len(plan["operations"])} files will be written; shared name/lore files include every queued edit.',
                      'Original ISOs will remain unchanged. Only listed gameplay edits are applied; other target gameplay stays unchanged.',
                      'Boot the new ISO and test every swapped Persona from an in-game save.']
            self.set_report('\n'.join(lines))
            self.status.set('Queue checks passed. Choose Build new ISO when ready.')
        self.run(lambda: batch_plan(source, target, entries), done)

    def build(self):
        try:
            if self.plan_key != self.key():
                raise FormatError('Check the queue before building.')
            source, target, entries = self.queue_selection()
        except (FormatError, OSError) as exc:
            messagebox.showinfo('Check the queue', str(exc))
            return
        filename = f'{target.game["key"]}_batch_{len(entries)}_swaps_TEST.iso'
        path = filedialog.asksaveasfilename(title='Build the queued swaps into a new ISO', defaultextension='.iso',
                                          initialfile=filename, filetypes=[('ISO images', '*.iso')])
        if not path:
            return
        self.invalidate(force=True)
        self.progress.set(0)
        def progress(stage, percent):
            self.events.put(('progress', stage, percent))
        def done(plan):
            self.progress.set(100)
            self.status.set(f'New ISO built and verified with {plan["swap_count"]} queued swap(s).')
            self.set_report(f'Created:\n{path}\n\nVerification report:\n{Path(path).with_suffix(".manifest.json")}\n\n'
                            f'All {plan["swap_count"]} queued swaps are included. The queue is kept for reuse.\n\n'
                            'Boot normally in PCSX2 and load an in-game save. Check the menu, Compendium, lore, '
                            'battle and skills for every swapped Persona. File verification passed; in-game testing is still required.')
            messagebox.showinfo('Build complete', f'The new ISO includes all {plan["swap_count"]} queued swap(s).\n\nBoot normally in PCSX2; use an in-game save.')
        self.run(lambda: build_batch(source, target, entries, path, progress, self.cancel_event.is_set), done, True)

    def close(self):
        if self.busy:
            messagebox.showinfo('Operation running', 'Let the check finish, or cancel the build before closing.')
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
