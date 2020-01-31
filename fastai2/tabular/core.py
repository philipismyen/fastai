# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/40_tabular.core.ipynb (unless otherwise specified).

__all__ = ['make_date', 'add_datepart', 'add_elapsed_times', 'cont_cat_split', 'Tabular', 'TabularPandas',
           'TabularProc', 'Categorify', 'setups', 'encodes', 'decodes', 'NormalizeTab', 'setups', 'encodes', 'decodes',
           'FillStrategy', 'FillMissing', 'ReadTabBatch', 'TabDataLoader', 'encodes', 'decodes']

# Cell
from ..torch_basics import *
from ..data.all import *

# Cell
pd.set_option('mode.chained_assignment','raise')

# Cell
def make_date(df, date_field):
    "Make sure `df[date_field]` is of the right date type."
    field_dtype = df[date_field].dtype
    if isinstance(field_dtype, pd.core.dtypes.dtypes.DatetimeTZDtype):
        field_dtype = np.datetime64
    if not np.issubdtype(field_dtype, np.datetime64):
        df[date_field] = pd.to_datetime(df[date_field], infer_datetime_format=True)

# Cell
def add_datepart(df, field_name, prefix=None, drop=True, time=False):
    "Helper function that adds columns relevant to a date in the column `field_name` of `df`."
    make_date(df, field_name)
    field = df[field_name]
    prefix = ifnone(prefix, re.sub('[Dd]ate$', '', field_name))
    attr = ['Year', 'Month', 'Week', 'Day', 'Dayofweek', 'Dayofyear', 'Is_month_end', 'Is_month_start',
            'Is_quarter_end', 'Is_quarter_start', 'Is_year_end', 'Is_year_start']
    if time: attr = attr + ['Hour', 'Minute', 'Second']
    for n in attr: df[prefix + n] = getattr(field.dt, n.lower())
    df[prefix + 'Elapsed'] = field.astype(np.int64) // 10 ** 9
    if drop: df.drop(field_name, axis=1, inplace=True)
    return df

# Cell
def _get_elapsed(df,field_names, date_field, base_field, prefix):
    for f in field_names:
        day1 = np.timedelta64(1, 'D')
        last_date,last_base,res = np.datetime64(),None,[]
        for b,v,d in zip(df[base_field].values, df[f].values, df[date_field].values):
            if last_base is None or b != last_base:
                last_date,last_base = np.datetime64(),b
            if v: last_date = d
            res.append(((d-last_date).astype('timedelta64[D]') / day1))
        df[prefix + f] = res
    return df

# Cell
def add_elapsed_times(df, field_names, date_field, base_field):
    "Add in `df` for each event in `field_names` the elapsed time according to `date_field` grouped by `base_field`"
    field_names = list(L(field_names))
    #Make sure date_field is a date and base_field a bool
    df[field_names] = df[field_names].astype('bool')
    make_date(df, date_field)

    work_df = df[field_names + [date_field, base_field]]
    work_df = work_df.sort_values([base_field, date_field])
    work_df = _get_elapsed(work_df, field_names, date_field, base_field, 'After')
    work_df = work_df.sort_values([base_field, date_field], ascending=[True, False])
    work_df = _get_elapsed(work_df, field_names, date_field, base_field, 'Before')

    for a in ['After' + f for f in field_names] + ['Before' + f for f in field_names]:
        work_df[a] = work_df[a].fillna(0).astype(int)

    for a,s in zip([True, False], ['_bw', '_fw']):
        work_df = work_df.set_index(date_field)
        tmp = (work_df[[base_field] + field_names].sort_index(ascending=a)
                      .groupby(base_field).rolling(7, min_periods=1).sum())
        tmp.drop(base_field,1,inplace=True)
        tmp.reset_index(inplace=True)
        work_df.reset_index(inplace=True)
        work_df = work_df.merge(tmp, 'left', [date_field, base_field], suffixes=['', s])
    work_df.drop(field_names,1,inplace=True)
    return df.merge(work_df, 'left', [date_field, base_field])

# Cell
def cont_cat_split(df, max_card=20, dep_var=None):
    "Helper function that returns column names of cont and cat variables from given `df`."
    cont_names, cat_names = [], []
    for label in df:
        if label == dep_var: continue
        if df[label].dtype == int and df[label].unique().shape[0] > max_card or df[label].dtype == float:
            cont_names.append(label)
        else: cat_names.append(label)
    return cont_names, cat_names

# Cell
class _TabIloc:
    "Get/set rows by iloc and cols by name"
    def __init__(self,to): self.to = to
    def __getitem__(self, idxs):
        df = self.to.items
        if isinstance(idxs,tuple):
            rows,cols = idxs
            cols = df.columns.isin(cols) if is_listy(cols) else df.columns.get_loc(cols)
        else: rows,cols = idxs,slice(None)
        return self.to.new(df.iloc[rows, cols])

# Cell
class Tabular(CollBase, GetAttr, FilteredBase):
    "A `DataFrame` wrapper that knows which cols are cont/cat/y, and returns rows in `__getitem__`"
    _default,with_cont='procs',True
    def __init__(self, df, procs=None, cat_names=None, cont_names=None, y_names=None, block_y=None, splits=None,
                 do_setup=True, device=None):
        if splits is None: splits=[range_of(df)]
        df = df.iloc[sum(splits, [])].copy()
        self.dataloaders = delegates(self._dl_type.__init__)(self.dataloaders)
        super().__init__(df)

        self.y_names,self.device = L(y_names),device
        if block_y is None and self.y_names:
            # Make ys categorical if they're not numeric
            ys = df[self.y_names]
            if len(ys.select_dtypes(include='number').columns)!=len(ys.columns): block_y = CategoryBlock()
        if block_y is not None and do_setup:
            if callable(block_y): block_y = block_y()
            procs = L(procs) + block_y.type_tfms
        self.cat_names,self.cont_names,self.procs = L(cat_names),L(cont_names),Pipeline(procs, as_item=True)
        self.split = len(splits[0])
        if do_setup: self.setup()

    def new(self, df):
        return type(self)(df, do_setup=False, block_y=TransformBlock(),
                          **attrdict(self, 'procs','cat_names','cont_names','y_names', 'device'))

    def subset(self, i): return self.new(self.items[slice(0,self.split) if i==0 else slice(self.split,len(self))])
    def copy(self): self.items = self.items.copy(); return self
    def decode(self): return self.procs.decode(self)
    def decode_row(self, row): return self.new(pd.DataFrame(row).T).decode().items.iloc[0]
    def show(self, max_n=10, **kwargs): display_df(self.new(self.all_cols[:max_n]).decode().items)
    def setup(self): self.procs.setup(self)
    def process(self): self.procs(self)
    def loc(self): return self.items.loc
    def iloc(self): return _TabIloc(self)
    def targ(self): return self.items[self.y_names]
    def x_names (self): return self.cat_names + self.cont_names
    def all_col_names (self): return self.x_names + self.y_names
    def n_subsets(self): return 2
    def y(self): return self[self.y_names[0]]
    def new_empty(self): return self.new(pd.DataFrame({}, columns=self.items.columns))
    def to_device(self, d=None):
        self.device = d
        return self

properties(Tabular,'loc','iloc','targ','all_col_names','n_subsets','x_names','y')

# Cell
class TabularPandas(Tabular):
    def transform(self, cols, f): self[cols] = self[cols].transform(f)

# Cell
def _add_prop(cls, nm):
    @property
    def f(o): return o[list(getattr(o,nm+'_names'))]
    @f.setter
    def fset(o, v): o[getattr(o,nm+'_names')] = v
    setattr(cls, nm+'s', f)
    setattr(cls, nm+'s', fset)

_add_prop(Tabular, 'cat')
_add_prop(Tabular, 'cont')
_add_prop(Tabular, 'y')
_add_prop(Tabular, 'x')
_add_prop(Tabular, 'all_col')

# Cell
class TabularProc(InplaceTransform):
    "Base class to write a non-lazy tabular processor for dataframes"
    def setup(self, items=None, train_setup=False): #TODO: properly deal with train_setup
        super().setup(getattr(items,'train',items), train_setup=False)
        # Procs are called as soon as data is available
        return self(items.items if isinstance(items,Datasets) else items)

# Cell
def _apply_cats (voc, add, c):
    if not is_categorical_dtype(c):
        return pd.Categorical(c, categories=voc[c.name][add:]).codes+add
    return c.cat.codes+add #if is_categorical_dtype(c) else c.map(voc[c.name].o2i)
def _decode_cats(voc, c): return c.map(dict(enumerate(voc[c.name].items)))

# Cell
class Categorify(TabularProc):
    "Transform the categorical variables to that type."
    order = 1
    def setups(self, to):
        self.classes = {n:CategoryMap(to.iloc[:,n].items, add_na=(n in to.cat_names)) for n in to.cat_names}

    def encodes(self, to): to.transform(to.cat_names, partial(_apply_cats, self.classes, 1))
    def decodes(self, to): to.transform(to.cat_names, partial(_decode_cats, self.classes))
    def __getitem__(self,k): return self.classes[k]

# Cell
@Categorize
def setups(self, to:Tabular):
    if len(to.y_names) > 0:
        self.vocab = CategoryMap(getattr(to, 'train', to).iloc[:,to.y_names[0]].items)
        self.c = len(self.vocab)
    return self(to)

@Categorize
def encodes(self, to:Tabular):
    to.transform(to.y_names, partial(_apply_cats, {n: self.vocab for n in to.y_names}, 0))
    return to

@Categorize
def decodes(self, to:Tabular):
    to.transform(to.y_names, partial(_decode_cats, {n: self.vocab for n in to.y_names}))
    return to

# Cell
class NormalizeTab(TabularProc):
    "Normalize the continuous variables."
    order = 2
    def setups(self, dsets): self.means,self.stds = dsets.conts.mean(),dsets.conts.std(ddof=0)+1e-7
    def encodes(self, to): to.conts = (to.conts-self.means) / self.stds
    def decodes(self, to): to.conts = (to.conts*self.stds ) + self.means

# Cell
@Normalize
def setups(self, to:Tabular):
    self.means,self.stds = getattr(to, 'train', to).conts.mean(),getattr(to, 'train', to).conts.std(ddof=0)+1e-7
    return self(to)

@Normalize
def encodes(self, to:Tabular):
    to.conts = (to.conts-self.means) / self.stds
    return to

@Normalize
def decodes(self, to:Tabular):
    to.conts = (to.conts*self.stds ) + self.means
    return to

# Cell
class FillStrategy:
    "Namespace containing the various filling strategies."
    def median  (c,fill): return c.median()
    def constant(c,fill): return fill
    def mode    (c,fill): return c.dropna().value_counts().idxmax()

# Cell
class FillMissing(TabularProc):
    "Fill the missing values in continuous columns."
    def __init__(self, fill_strategy=FillStrategy.median, add_col=True, fill_vals=None):
        if fill_vals is None: fill_vals = defaultdict(int)
        store_attr(self, 'fill_strategy,add_col,fill_vals')

    def setups(self, dsets):
        self.na_dict = {n:self.fill_strategy(dsets[n], self.fill_vals[n])
                        for n in pd.isnull(dsets.conts).any().keys()}

    def encodes(self, to):
        missing = pd.isnull(to.conts)
        for n in missing.any().keys():
            assert n in self.na_dict, f"nan values in `{n}` but not in setup training set"
            to[n].fillna(self.na_dict[n], inplace=True)
            if self.add_col:
                to.loc[:,n+'_na'] = missing[n]
                if n+'_na' not in to.cat_names: to.cat_names.append(n+'_na')

# Cell
def _maybe_expand(o): return o[:,None] if o.ndim==1 else o

# Cell
class ReadTabBatch(ItemTransform):
    def __init__(self, to): self.to = to

    def encodes(self, to):
        if not to.with_cont: res = tensor(to.cats).long(), tensor(to.targ)
        else: res = (tensor(to.cats).long(),tensor(to.conts).float(), tensor(to.targ))
        if to.device is not None: res = to_device(res, to.device)
        return res

    def decodes(self, o):
        o = [_maybe_expand(o_) for o_ in to_np(o) if o_.size != 0]
        vals = np.concatenate(o, axis=1)
        df = pd.DataFrame(vals, columns=self.to.all_col_names)
        to = self.to.new(df)
        return to

# Cell
@typedispatch
def show_batch(x: Tabular, y, its, max_n=10, ctxs=None):
    x.show()

# Cell
@delegates()
class TabDataLoader(TfmdDL):
    do_item = noops
    def __init__(self, dataset, bs=16, shuffle=False, after_batch=None, num_workers=0, **kwargs):
        if after_batch is None: after_batch = L(TransformBlock().batch_tfms)+ReadTabBatch(dataset)
        super().__init__(dataset, bs=bs, shuffle=shuffle, after_batch=after_batch, num_workers=num_workers, **kwargs)

    def create_batch(self, b): return self.dataset.iloc[b]

TabularPandas._dl_type = TabDataLoader

# Cell
@EncodedMultiCategorize
def encodes(self, to:Tabular): return to

@EncodedMultiCategorize
def decodes(self, to:Tabular):
    to.transform(to.y_names, lambda c: c==1)
    return to