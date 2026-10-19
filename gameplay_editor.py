"""Native dialog for target-game stats, elemental affinities and skill progression."""
import tkinter as tk
from tkinter import ttk, messagebox
from gameplay import GameplayEdit, Skill, AFFINITIES, tables_for
from persona_porter import FormatError

CAMPAIGN_LABELS = {'journey': 'The Journey', 'answer': 'The Answer', 'main': 'Persona 4'}


class GameplayDialog(tk.Toplevel):
    def __init__(self, parent, game, index, edits=()):
        self.tables = tables_for(game)
        self.records, unavailable = {}, []
        for campaign in self.tables.campaigns:
            try:
                self.records[campaign] = self.tables.record(index, campaign)
            except FormatError as exc:
                unavailable.append(f'{CAMPAIGN_LABELS[campaign]}: {exc}')
        if not self.records:
            raise FormatError('\n'.join(unavailable))
        super().__init__(parent)
        self.title(f'Gameplay — {game.name(index)} [{index:03X}] — {game.title}')
        self.geometry('860x680')
        self.minsize(760, 620)
        self.transient(parent)
        self.index = index
        self.result = None
        self.saved = {edit.campaign: edit for edit in edits}
        self.current = next(iter(self.records))
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text='Enable the sections you want to edit. All skill names come from the target game.',
                  wraplength=790).grid(row=0, column=0, sticky='w', pady=(0, 10))
        header = ttk.Frame(body)
        header.grid(row=1, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(header, text='Campaign').pack(side='left', padx=(0, 10))
        self.campaign = tk.StringVar(value=CAMPAIGN_LABELS[self.current])
        self.campaign_box = ttk.Combobox(header, state='readonly', textvariable=self.campaign,
                                        values=[CAMPAIGN_LABELS[c] for c in self.records], width=22)
        self.campaign_box.pack(side='left')
        self.campaign_box.bind('<<ComboboxSelected>>', self.switch_campaign)
        ttk.Button(header, text='Reset this campaign to target', command=self.reset_campaign).pack(side='right')
        tabs = ttk.Notebook(body)
        tabs.grid(row=2, column=0, sticky='nsew')
        tabs.enable_traversal()
        self.flags = {key: tk.BooleanVar() for key in ('stats', 'affinities', 'skills')}
        self.controls = {key: [] for key in self.flags}
        stats = ttk.Frame(tabs, padding=16)
        affinities = ttk.Frame(tabs, padding=16)
        skills = ttk.Frame(tabs, padding=16)
        for frame, label in ((stats, 'Base stats'), (affinities, 'Resistances'), (skills, 'Starting / level-up skills')):
            tabs.add(frame, text=label)
        for frame, key, label in ((stats, 'stats', 'Apply edited base level and stats'),
                                  (affinities, 'affinities', 'Apply edited elemental affinities'),
                                  (skills, 'skills', 'Apply this starting / level-up skill list')):
            ttk.Checkbutton(frame, text=label, variable=self.flags[key], command=self.update_enabled).grid(
                row=0, column=0, columnspan=4, sticky='w', pady=(0, 12))
        self.stat_vars = []
        for row, label in enumerate(('Base level', 'Strength (STR)', 'Magic (MAG)', 'Endurance (END)', 'Agility (AG)', 'Luck (LU)'), 1):
            var = tk.StringVar()
            self.stat_vars.append(var)
            ttk.Label(stats, text=label, width=22).grid(row=row, column=0, sticky='w', pady=5)
            spin = ttk.Spinbox(stats, from_=1, to=99, textvariable=var, width=8)
            spin.grid(row=row, column=1, sticky='w')
            self.controls['stats'].append(spin)
        ttk.Label(stats, text='Changing base level keeps the existing learning offsets unless you also edit the skill list. '
                  'Stat growth weights, arcana and inheritance stay unchanged.', wraplength=740).grid(
                      row=7, column=0, columnspan=3, sticky='w', pady=16)
        self.affinity_vars, self.affinity_boxes, self.affinity_choices = {}, {}, {}
        for row, (element, _) in enumerate(self.tables.elements, 1):
            ttk.Label(affinities, text=element, width=18).grid(row=row, column=0, sticky='w', pady=4)
            var = tk.StringVar()
            combo = ttk.Combobox(affinities, state='readonly', textvariable=var, width=30)
            combo.grid(row=row, column=1, sticky='w')
            self.affinity_vars[element], self.affinity_boxes[element] = var, combo
            self.controls['affinities'].append(combo)
        ttk.Label(affinities, text='Almighty and ailment responses stay unchanged. Unusual original values can be kept as shown.',
                  wraplength=740).grid(row=11, column=0, columnspan=3, sticky='w', pady=12)
        skills.columnconfigure(1, weight=1)
        skills.rowconfigure(1, weight=1)
        self.tree = ttk.Treeview(skills, columns=('level', 'skill'), show='headings', height=10, selectmode='browse')
        self.tree.heading('level', text='When learned')
        self.tree.heading('skill', text='Target-game skill')
        self.tree.column('level', width=120, stretch=False)
        self.tree.column('skill', width=430)
        self.tree.grid(row=1, column=0, columnspan=4, sticky='nsew')
        scroll = ttk.Scrollbar(skills, orient='vertical', command=self.tree.yview)
        scroll.grid(row=1, column=4, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.select_skill)
        self.kind = tk.StringVar(value='Starting skill')
        self.kind_box = ttk.Combobox(skills, state='readonly', textvariable=self.kind,
                                     values=('Starting skill', 'Learn at level'), width=18)
        self.kind_box.grid(row=2, column=0, sticky='w', pady=10)
        self.kind_box.bind('<<ComboboxSelected>>', lambda _: self.update_enabled())
        self.learn_level = tk.StringVar(value='2')
        self.level_spin = ttk.Spinbox(skills, from_=1, to=99, textvariable=self.learn_level, width=7)
        self.level_spin.grid(row=2, column=1, sticky='w', padx=10)
        self.skill_var = tk.StringVar()
        self.skill_labels = {f'{name} [{i:03X}]': i for i, name in sorted(self.tables.skill_names.items(), key=lambda item:(item[1].casefold(), item[0]))}
        self.skill_box = ttk.Combobox(skills, textvariable=self.skill_var, values=list(self.skill_labels), width=40)
        self.skill_box.grid(row=3, column=0, columnspan=4, sticky='ew')
        self.skill_box.bind('<KeyRelease>', self.filter_skills)
        buttons = ttk.Frame(skills)
        buttons.grid(row=4, column=0, columnspan=4, sticky='w', pady=10)
        for label, action in (('Add skill', self.add_skill), ('Update selected', self.update_skill), ('Remove selected', self.remove_skill)):
            button = ttk.Button(buttons, text=label, command=action)
            button.pack(side='left', padx=(0, 8))
            self.controls['skills'].append(button)
        self.controls['skills'].extend((self.kind_box, self.skill_box))
        ttk.Label(skills, text=f'Type part of a skill name, then choose it from the menu. Up to eight starting skills; '
                  f'{15 if self.tables.p3 else 16} entries total. Level-up skills use the actual level to unlock.',
                  wraplength=740).grid(row=5, column=0, columnspan=4, sticky='w')
        self.note = ttk.Label(body, wraplength=790)
        self.note.grid(row=3, column=0, sticky='w', pady=(10, 0))
        if unavailable:
            self.note.configure(text='Some campaigns are unavailable for this slot. ' + '\n'.join(unavailable))
        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, sticky='e', pady=(12, 0))
        ttk.Button(actions, text='Cancel', command=self.destroy).pack(side='left', padx=8)
        ttk.Button(actions, text='Save gameplay choices', command=self.save).pack(side='left')
        modifier = 'Command' if self.tk.call('tk', 'windowingsystem') == 'aqua' else 'Control'
        for key, frame in (('1', stats), ('2', affinities), ('3', skills)):
            def select_tab(event, panel=frame):
                tabs.select(panel)
                return 'break'
            self.bind(f'<{modifier}-{key}>', select_tab)
        def save_key(event):
            self.save()
            return 'break'
        self.bind(f'<{modifier}-s>', save_key)
        self.bind('<Escape>', lambda _: self.destroy())
        self.load_campaign()
        self.wait_visibility()
        self.grab_set()
        self.focus_set()
        self.wait_window()

    def load_campaign(self):
        record = self.records[self.current]
        edit = self.saved.get(self.current)
        for key in self.flags:
            self.flags[key].set(edit is not None and getattr(edit, key) is not None)
        values = (record.level, *record.stats) if edit is None or edit.stats is None else (edit.level, *edit.stats)
        for var, value in zip(self.stat_vars, values):
            var.set(str(value))
        original = dict(record.affinities)
        affinities = original if edit is None or edit.affinities is None else dict(edit.affinities)
        for element in self.affinity_vars:
            choices = dict(AFFINITIES)
            if original[element] not in choices.values():
                choices[f'Original (0x{original[element]:04X})'] = original[element]
            self.affinity_choices[element] = choices
            self.affinity_boxes[element].configure(values=list(choices))
            self.affinity_vars[element].set(next(label for label, value in choices.items() if value == affinities[element]))
        self.skill_entries = list(record.skills if edit is None or edit.skills is None else edit.skills)
        self.refresh_skills()
        self.skill_var.set('')
        self.skill_box.configure(values=list(self.skill_labels))
        self.update_enabled()

    def update_enabled(self):
        for key, controls in self.controls.items():
            for control in controls:
                if not self.flags[key].get():
                    control.configure(state='disabled')
                else:
                    control.configure(state='readonly' if control in (*self.affinity_boxes.values(), self.kind_box) else 'normal')
        self.level_spin.configure(state='normal' if self.flags['skills'].get() and self.kind.get() == 'Learn at level' else 'disabled')

    def collect(self):
        if not any(var.get() for var in self.flags.values()):
            return None
        level, stats, affinities, skills = None, None, None, None
        try:
            if self.flags['stats'].get():
                values = tuple(int(var.get()) for var in self.stat_vars)
                level, stats = values[0], values[1:]
            if self.flags['affinities'].get():
                affinities = tuple((name, self.affinity_choices[name][var.get()]) for name, var in self.affinity_vars.items())
            if self.flags['skills'].get():
                skills = tuple(self.skill_entries)
        except (ValueError, KeyError) as exc:
            raise FormatError('Enter whole numbers and choose affinities from their menus') from exc
        edit = GameplayEdit(self.current, level, stats, affinities, skills)
        self.tables.validate(self.index, edit)
        return edit

    def remember(self):
        edit = self.collect()
        if edit is None:
            self.saved.pop(self.current, None)
        else:
            self.saved[self.current] = edit

    def switch_campaign(self, _):
        try:
            self.remember()
        except FormatError as exc:
            self.campaign.set(CAMPAIGN_LABELS[self.current])
            messagebox.showerror('Check gameplay choices', str(exc), parent=self)
            return
        self.current = next(c for c in self.records if CAMPAIGN_LABELS[c] == self.campaign.get())
        self.load_campaign()

    def reset_campaign(self):
        self.saved.pop(self.current, None)
        self.load_campaign()

    def save(self):
        try:
            self.remember()
        except FormatError as exc:
            messagebox.showerror('Check gameplay choices', str(exc), parent=self)
            return
        self.result = tuple(self.saved[c] for c in self.tables.campaigns if c in self.saved)
        self.destroy()

    def refresh_skills(self):
        self.tree.delete(*self.tree.get_children())
        for i, skill in enumerate(self.skill_entries):
            name = self.tables.skill_names.get(skill.skill_id, f'Original skill {skill.skill_id:03X}')
            self.tree.insert('', 'end', iid=str(i), values=('Start' if not skill.level else f'Level {skill.level}',
                                                         f'{name} [{skill.skill_id:03X}]'))

    def select_skill(self, _):
        selected = self.tree.selection()
        if not selected:
            return
        skill = self.skill_entries[int(selected[0])]
        self.kind.set('Starting skill' if not skill.level else 'Learn at level')
        self.learn_level.set(str(skill.level or min(99, self.records[self.current].level+1)))
        self.skill_var.set(f'{self.tables.skill_names.get(skill.skill_id, "Original skill")} [{skill.skill_id:03X}]')
        self.update_enabled()

    def filter_skills(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Escape', 'Tab'):
            return
        query = self.skill_var.get().strip().casefold()
        self.skill_box.configure(values=[label for label in self.skill_labels if query in label.casefold()])

    def picked_skill(self):
        label = self.skill_var.get()
        if label not in self.skill_labels:
            exact = [text for text, i in self.skill_labels.items() if self.tables.skill_names[i].casefold() == label.strip().casefold()]
            if len(exact) != 1:
                raise FormatError('Choose a named target-game skill from the menu')
            label = exact[0]
        try:
            level = 0 if self.kind.get() == 'Starting skill' else int(self.learn_level.get())
        except ValueError as exc:
            raise FormatError('Enter a whole-number unlock level') from exc
        if self.kind.get() == 'Learn at level' and not 1 <= level <= 99:
            raise FormatError('Choose an unlock level from 1 to 99, or choose Starting skill')
        return Skill(self.skill_labels[label], level)

    def change_skill(self, update=False):
        if not self.flags['skills'].get():
            return
        try:
            skill = self.picked_skill()
            proposed = list(self.skill_entries)
            if update:
                selected = self.tree.selection()
                if not selected:
                    raise FormatError('Select a skill entry to update')
                proposed[int(selected[0])] = skill
            else:
                proposed.append(skill)
            original = self.skill_entries
            self.skill_entries = proposed
            try:
                self.collect()
            except FormatError:
                self.skill_entries = original
                raise
            self.refresh_skills()
        except FormatError as exc:
            messagebox.showerror('Check skill choices', str(exc), parent=self)

    def add_skill(self):
        self.change_skill()

    def update_skill(self):
        self.change_skill(True)

    def remove_skill(self):
        if self.flags['skills'].get() and self.tree.selection():
            del self.skill_entries[int(self.tree.selection()[0])]
            self.refresh_skills()
