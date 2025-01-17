import os.path as osp
import warnings
from typing import Optional

import pytest
import torch
from torch import Tensor, tensor

import torch_geometric
from torch_geometric.data import EdgeIndex
from torch_geometric.data.edge_index import (
    SUPPORTED_DTYPES,
    ReduceType,
    _torch_sparse_spmm,
    _TorchSPMM,
)
from torch_geometric.profile import benchmark
from torch_geometric.testing import (
    disableExtensions,
    onlyCUDA,
    onlyLinux,
    withCUDA,
    withPackage,
)
from torch_geometric.typing import SparseTensor
from torch_geometric.utils import scatter

DTYPES = [pytest.param(dtype, id=str(dtype)[6:]) for dtype in SUPPORTED_DTYPES]
IS_UNDIRECTED = [
    pytest.param(False, id='directed'),
    pytest.param(True, id='undirected'),
]
TRANSPOSE = [
    pytest.param(False, id=''),
    pytest.param(True, id='transpose'),
]


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_basic(dtype, device):
    kwargs = dict(dtype=dtype, device=device, sparse_size=(3, 3))
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], **kwargs)
    adj.validate()
    assert isinstance(adj, EdgeIndex)
    assert str(adj).startswith('EdgeIndex([[0, 1, 1, 2],')
    assert adj.dtype == dtype
    assert adj.device == device
    assert adj.sparse_size() == (3, 3)

    assert adj.sort_order is None
    assert not adj.is_sorted
    assert not adj.is_sorted_by_row
    assert not adj.is_sorted_by_col

    assert not adj.is_undirected

    out = adj.as_tensor()
    assert not isinstance(out, EdgeIndex)
    assert out.dtype == dtype
    assert out.device == device

    out = adj + 1
    assert not isinstance(out, EdgeIndex)
    assert out.dtype == dtype
    assert out.device == device


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_undirected(dtype, device):
    kwargs = dict(dtype=dtype, device=device, is_undirected=True)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], **kwargs)
    assert isinstance(adj, EdgeIndex)
    assert adj.is_undirected

    assert adj.sparse_size() == (None, None)
    adj.get_num_rows()
    assert adj.sparse_size() == (3, 3)

    adj.validate()

    with pytest.raises(ValueError, match="'EdgeIndex' is not undirected"):
        EdgeIndex([[0, 1, 1, 2], [0, 0, 1, 1]], **kwargs).validate()


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_fill_cache_(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    adj.validate().fill_cache_()
    assert adj.sparse_size() == (3, 3)
    assert adj._indptr.dtype == dtype
    assert adj._indptr.equal(tensor([0, 1, 3, 4], device=device))
    assert adj._T_perm.dtype == torch.int64
    assert (adj._T_perm.equal(tensor([1, 0, 3, 2], device=device))
            or adj._T_perm.equal(tensor([1, 3, 0, 2], device=device)))
    assert adj._T_index[0].dtype == dtype
    assert (adj._T_index[0].equal(tensor([1, 0, 2, 1], device=device))
            or adj._T_index[0].equal(tensor([1, 2, 0, 1], device=device)))
    assert adj._T_index[1].dtype == dtype
    assert adj._T_index[1].equal(tensor([0, 1, 1, 2], device=device))
    if is_undirected:
        assert adj._T_indptr is None
    else:
        assert adj._T_indptr.dtype == dtype
        assert adj._T_indptr.equal(tensor([0, 1, 3, 4], device=device))

    adj = EdgeIndex([[1, 0, 2, 1], [0, 1, 1, 2]], sort_order='col', **kwargs)
    adj.validate().fill_cache_()
    assert adj.sparse_size() == (3, 3)
    assert adj._indptr.dtype == dtype
    assert adj._indptr.equal(tensor([0, 1, 3, 4], device=device))
    assert (adj._T_perm.equal(tensor([1, 0, 3, 2], device=device))
            or adj._T_perm.equal(tensor([1, 3, 0, 2], device=device)))
    assert adj._T_index[0].dtype == dtype
    assert adj._T_index[0].equal(tensor([0, 1, 1, 2], device=device))
    assert adj._T_index[1].dtype == dtype
    assert (adj._T_index[1].equal(tensor([1, 0, 2, 1], device=device))
            or adj._T_index[1].equal(tensor([1, 2, 0, 1], device=device)))
    if is_undirected:
        assert adj._T_indptr is None
    else:
        assert adj._T_indptr.dtype == dtype
        assert adj._T_indptr.equal(tensor([0, 1, 3, 4], device=device))


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_clone(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)

    out = adj.clone()
    assert isinstance(out, EdgeIndex)
    assert out.dtype == dtype
    assert out.device == device
    assert out.is_sorted_by_row
    assert out.is_undirected == is_undirected

    out = torch.clone(adj)
    assert isinstance(out, EdgeIndex)
    assert out.dtype == dtype
    assert out.device == device
    assert out.is_sorted_by_row
    assert out.is_undirected == is_undirected


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_to(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    adj.fill_cache_()

    adj = adj.to(device)
    assert isinstance(adj, EdgeIndex)
    assert adj.device == device
    assert adj._indptr.device == device
    assert adj._T_perm.device == device

    out = adj.to(torch.int)
    assert out.dtype == torch.int
    if torch_geometric.typing.WITH_PT20:
        assert isinstance(out, EdgeIndex)
        assert out._indptr.dtype == torch.int
        assert out._T_perm.dtype == torch.int
    else:
        assert not isinstance(out, EdgeIndex)

    out = adj.to(torch.float)
    assert not isinstance(out, EdgeIndex)
    assert out.dtype == torch.float

    out = adj.long()
    assert isinstance(out, EdgeIndex)
    assert out.dtype == torch.int64

    out = adj.int()
    assert out.dtype == torch.int
    if torch_geometric.typing.WITH_PT20:
        assert isinstance(out, EdgeIndex)
    else:
        assert not isinstance(out, EdgeIndex)


@onlyCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_cpu_cuda(dtype):
    kwargs = dict(dtype=dtype)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], **kwargs)

    out = adj.cuda()
    assert isinstance(out, EdgeIndex)
    assert out.is_cuda

    out = out.cpu()
    assert isinstance(out, EdgeIndex)
    assert not out.is_cuda


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_share_memory(dtype, device):
    kwargs = dict(dtype=dtype, device=device)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    adj.fill_cache_()

    adj = adj.share_memory_()
    assert isinstance(adj, EdgeIndex)
    assert adj.is_shared()
    assert adj._indptr.is_shared()


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_contiguous(dtype, device):
    kwargs = dict(dtype=dtype, device=device)
    data = tensor([[0, 1], [1, 0], [1, 2], [2, 1]], **kwargs).t()

    with pytest.raises(ValueError, match="needs to be contiguous"):
        EdgeIndex(data)

    adj = EdgeIndex(data.contiguous()).contiguous()
    assert isinstance(adj, EdgeIndex)
    assert adj.is_contiguous()


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_sort_by(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    out = adj.sort_by('row')
    assert isinstance(out, torch.return_types.sort)
    assert isinstance(out.values, EdgeIndex)
    assert not isinstance(out.indices, EdgeIndex)
    assert out.values.equal(adj)
    assert out.indices == slice(None, None, None)

    adj = EdgeIndex([[0, 1, 2, 1], [1, 0, 1, 2]], **kwargs)
    out = adj.sort_by('row')
    assert isinstance(out, torch.return_types.sort)
    assert isinstance(out.values, EdgeIndex)
    assert not isinstance(out.indices, EdgeIndex)
    assert out.values[0].equal(tensor([0, 1, 1, 2], device=device))
    assert (out.values[1].equal(tensor([1, 0, 2, 1], device=device))
            or out.values[1].equal(tensor([1, 2, 0, 1], device=device)))
    assert (out.indices.equal(tensor([0, 1, 3, 2], device=device))
            or out.indices.equal(tensor([0, 3, 1, 2], device=device)))

    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)

    out, perm = adj.sort_by('col')
    assert adj._T_perm is not None  # Check caches.
    assert adj._T_index[0] is not None and adj._T_index[1] is not None
    assert (out[0].equal(tensor([1, 0, 2, 1], device=device))
            or out[0].equal(tensor([1, 2, 0, 1], device=device)))
    assert out[1].equal(tensor([0, 1, 1, 2], device=device))
    assert (perm.equal(tensor([1, 0, 3, 2], device=device))
            or perm.equal(tensor([1, 3, 0, 2], device=device)))
    assert out._T_perm is None
    assert out._T_index[0] is None and out._T_index[1] is None

    out, perm = out.sort_by('row')
    assert out[0].equal(tensor([0, 1, 1, 2], device=device))
    assert (out[1].equal(tensor([1, 0, 2, 1], device=device))
            or out[1].equal(tensor([1, 2, 0, 1], device=device)))
    assert (perm.equal(tensor([1, 0, 3, 2], device=device))
            or perm.equal(tensor([2, 3, 0, 1], device=device)))
    assert out._T_perm is None
    assert out._T_index[0] is None and out._T_index[1] is None


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_cat(dtype, device, is_undirected):
    args = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj1 = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sparse_size=(3, 3), **args)
    adj2 = EdgeIndex([[1, 2, 2, 3], [2, 1, 3, 2]], sparse_size=(4, 4), **args)

    out = torch.cat([adj1, adj2], dim=1)
    assert out.size() == (2, 8)
    assert isinstance(out, EdgeIndex)
    assert out.sparse_size() == (4, 4)
    assert not out.is_sorted
    assert out.is_undirected == is_undirected

    out = torch.cat([adj1, adj2], dim=0)
    assert out.size() == (4, 4)
    assert not isinstance(out, EdgeIndex)


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_flip(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    adj.fill_cache_()

    out = adj.flip(0)
    assert isinstance(out, EdgeIndex)
    assert out.equal(tensor([[1, 0, 2, 1], [0, 1, 1, 2]], device=device))
    assert out.is_sorted_by_col
    assert out.is_undirected == is_undirected
    assert out._T_indptr.equal(tensor([0, 1, 3, 4], device=device))

    out = adj.flip([0, 1])
    assert isinstance(out, EdgeIndex)
    assert out.equal(tensor([[1, 2, 0, 1], [2, 1, 1, 0]], device=device))
    assert not out.is_sorted
    assert out.is_undirected == is_undirected
    assert out._T_indptr is None


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_index_select(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)

    out = adj.index_select(1, tensor([1, 3], device=device))
    assert out.equal(tensor([[1, 2], [0, 1]], device=device))
    assert isinstance(out, EdgeIndex)
    assert not out.is_sorted
    assert not out.is_undirected

    out = adj.index_select(0, tensor([0], device=device))
    assert out.equal(tensor([[0, 1, 1, 2]], device=device))
    assert not isinstance(out, EdgeIndex)


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_narrow(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)

    out = adj.narrow(dim=1, start=1, length=2)
    assert isinstance(out, EdgeIndex)
    assert out.equal(tensor([[1, 1], [0, 2]], device=device))
    assert out.is_sorted_by_row
    assert not out.is_undirected

    out = adj.narrow(dim=0, start=0, length=1)
    assert not isinstance(out, EdgeIndex)
    assert out.equal(tensor([[0, 1, 1, 2]], device=device))


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('is_undirected', IS_UNDIRECTED)
def test_getitem(dtype, device, is_undirected):
    kwargs = dict(dtype=dtype, device=device, is_undirected=is_undirected)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)

    out = adj[:, tensor([False, True, False, True], device=device)]
    assert isinstance(out, EdgeIndex)
    assert out.equal(tensor([[1, 2], [0, 1]], device=device))
    assert out.is_sorted_by_row
    assert not out.is_undirected

    out = adj[..., tensor([1, 3], device=device)]
    assert isinstance(out, EdgeIndex)
    assert out.equal(tensor([[1, 2], [0, 1]], device=device))
    assert not out.is_sorted
    assert not out.is_undirected

    out = adj[..., 1::2]
    assert isinstance(out, EdgeIndex)
    assert out.equal(tensor([[1, 2], [0, 1]], device=device))
    assert out.is_sorted_by_row
    assert not out.is_undirected

    out = adj[:, 0]
    assert not isinstance(out, EdgeIndex)

    out = adj[tensor([0], device=device)]
    assert not isinstance(out, EdgeIndex)


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('value_dtype', [None, torch.double])
def test_to_dense(dtype, device, value_dtype):
    kwargs = dict(dtype=dtype, device=device)
    adj = EdgeIndex([[1, 0, 2, 1], [0, 1, 1, 2]], **kwargs)

    out = adj.to_dense(dtype=value_dtype)
    assert isinstance(out, Tensor)
    assert out.size() == (3, 3)
    expected = [[0.0, 1.0, 0.0], [1.0, 0.0, 1.0], [0.0, 1.0, 0.0]]
    assert out.equal(tensor(expected, dtype=value_dtype, device=device))

    value = torch.arange(1, 5, dtype=value_dtype or torch.float, device=device)
    out = adj.to_dense(value)
    assert isinstance(out, Tensor)
    assert out.size() == (3, 3)
    expected = [[0.0, 2.0, 0.0], [1.0, 0.0, 4.0], [0.0, 3.0, 0.0]]
    assert out.equal(tensor(expected, dtype=value_dtype, device=device))

    value = torch.arange(1, 5, dtype=value_dtype or torch.float, device=device)
    out = adj.to_dense(value.view(-1, 1))
    assert isinstance(out, Tensor)
    assert out.size() == (3, 3, 1)
    expected = [
        [[0.0], [2.0], [0.0]],
        [[1.0], [0.0], [4.0]],
        [[0.0], [3.0], [0.0]],
    ]
    assert out.equal(tensor(expected, dtype=value_dtype, device=device))


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_to_sparse_coo(dtype, device):
    kwargs = dict(dtype=dtype, device=device)
    adj = EdgeIndex([[1, 0, 2, 1], [0, 1, 1, 2]], **kwargs)
    if torch_geometric.typing.WITH_PT20:
        out = adj.to_sparse(layout=torch.sparse_coo)
    else:
        out = adj.to_sparse()
    assert isinstance(out, Tensor)
    assert out.dtype == torch.float
    assert out.device == device
    assert out.layout == torch.sparse_coo
    assert out.size() == (3, 3)
    assert adj.equal(out._indices())

    adj = EdgeIndex([[1, 0, 2, 1], [0, 1, 1, 2]], **kwargs)
    out = adj.to_sparse_coo()
    assert isinstance(out, Tensor)
    assert out.dtype == torch.float
    assert out.device == device
    assert out.layout == torch.sparse_coo
    assert out.size() == (3, 3)
    assert adj.equal(out._indices())


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_to_sparse_csr(dtype, device):
    kwargs = dict(dtype=dtype, device=device)
    with pytest.raises(ValueError, match="not sorted"):
        EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], **kwargs).to_sparse_csr()

    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    if torch_geometric.typing.WITH_PT20:
        out = adj.to_sparse(layout=torch.sparse_csr)
    else:
        out = adj.to_sparse_csr()
    assert isinstance(out, Tensor)
    assert out.dtype == torch.float
    assert out.device == device
    assert out.layout == torch.sparse_csr
    assert out.size() == (3, 3)
    assert adj._indptr.equal(out.crow_indices())
    assert adj[1].equal(out.col_indices())


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_to_sparse_csc(dtype, device):
    kwargs = dict(dtype=dtype, device=device)
    with pytest.raises(ValueError, match="not sorted"):
        EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], **kwargs).to_sparse_csc()

    adj = EdgeIndex([[1, 0, 2, 1], [0, 1, 1, 2]], sort_order='col', **kwargs)
    if torch_geometric.typing.WITH_PT20:
        out = adj.to_sparse(layout=torch.sparse_csc)
    else:
        out = adj.to_sparse_csc()
    assert isinstance(out, Tensor)
    assert out.dtype == torch.float
    assert out.device == device
    assert out.layout == torch.sparse_csc
    assert out.size() == (3, 3)
    assert adj._indptr.equal(out.ccol_indices())
    assert adj[0].equal(out.row_indices())


@withCUDA
@withPackage('torch_sparse')
def test_to_sparse_tensor(device):
    kwargs = dict(device=device)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], **kwargs)
    out = adj.to_sparse_tensor()
    assert isinstance(out, SparseTensor)
    assert out.sizes() == [3, 3]
    row, col, _ = out.coo()
    assert row.equal(adj[0])
    assert col.equal(adj[1])


@withCUDA
@withPackage('torch_sparse')
@pytest.mark.parametrize('reduce', ReduceType.__args__)
@pytest.mark.parametrize('transpose', TRANSPOSE)
def test_torch_sparse_spmm(device, reduce, transpose):
    adj = EdgeIndex([[0, 1, 1, 2], [2, 0, 1, 2]], device=device)
    adj = adj.sort_by('col' if transpose else 'row').values
    i = 0 if transpose else 1

    # Basic:
    x = torch.randn(3, 1, device=device)

    out = _torch_sparse_spmm(adj, x, None, reduce, transpose)
    exp = scatter(x[adj[i]], adj[1 - i], reduce=reduce)
    assert out.allclose(exp)

    # With non-zero values:
    x = torch.randn(3, 1, device=device)
    value = torch.rand(adj.size(1), device=device)

    out = _torch_sparse_spmm(adj, x, value, reduce, transpose)
    exp = scatter(x[adj[i]] * value.view(-1, 1), adj[1 - i], reduce=reduce)
    assert out.allclose(exp)

    # Gradients w.r.t. other:
    x1 = torch.randn(3, 1, device=device, requires_grad=True)
    x2 = x1.detach().requires_grad_()
    grad = torch.randn_like(x1)

    out = _torch_sparse_spmm(adj, x1, None, reduce, transpose)
    out.backward(grad)
    exp = scatter(x2[adj[i]], adj[1 - i], reduce=reduce)
    exp.backward(grad)
    assert x1.grad.allclose(x2.grad)

    # Gradients w.r.t. value:
    x = torch.randn(3, 1, device=device)
    value1 = torch.rand(adj.size(1), device=device, requires_grad=True)
    value2 = value1.detach().requires_grad_()
    grad = torch.randn_like(x)

    out = _torch_sparse_spmm(adj, x, value1, reduce, transpose)
    out.backward(grad)
    exp = scatter(x[adj[i]] * value2.view(-1, 1), adj[1 - i], reduce=reduce)
    exp.backward(grad)
    assert value1.grad.allclose(value2.grad)


@withCUDA
@pytest.mark.parametrize('reduce', ReduceType.__args__)
@pytest.mark.parametrize('transpose', TRANSPOSE)
def test_torch_spmm(device, reduce, transpose):
    adj = EdgeIndex([[0, 1, 1, 2], [2, 0, 1, 2]], device=device)
    adj, perm = adj.sort_by('col' if transpose else 'row')
    i = 0 if transpose else 1

    # Basic:
    x = torch.randn(3, 2, device=device)

    if (not x.is_cuda and torch_geometric.typing.WITH_PT20) or reduce == 'sum':
        out = _TorchSPMM.apply(adj, x, None, reduce, transpose)
        exp = scatter(x[adj[i]], adj[1 - i], reduce=reduce)
        assert out.allclose(exp)
    else:
        with pytest.raises(AssertionError):
            _TorchSPMM.apply(adj, x, None, reduce, transpose)

    # With non-zero values:
    x = torch.randn(3, 1, device=device)
    value = torch.rand(adj.size(1), device=device)

    if (not x.is_cuda and torch_geometric.typing.WITH_PT20) or reduce == 'sum':
        out = _TorchSPMM.apply(adj, x, value, reduce, transpose)
        exp = scatter(x[adj[i]] * value.view(-1, 1), adj[1 - i], reduce=reduce)
        assert out.allclose(exp)
    else:
        with pytest.raises(AssertionError):
            _TorchSPMM.apply(adj, x, value, reduce, transpose)

    # Gradients w.r.t. other:
    x1 = torch.randn(3, 1, device=device, requires_grad=True)
    x2 = x1.detach().requires_grad_()
    grad = torch.randn_like(x1)

    if reduce == 'sum':
        out = _TorchSPMM.apply(adj, x1, None, reduce, transpose)
        out.backward(grad)
        exp = scatter(x2[adj[i]], adj[1 - i], reduce=reduce)
        exp.backward(grad)
        assert x1.grad.allclose(x2.grad)
    else:
        with pytest.raises(AssertionError):
            out = _TorchSPMM.apply(adj, x1, None, reduce, transpose)
            out.backward(grad)

    # Gradients w.r.t. value:
    x = torch.randn(3, 1, device=device)
    value1 = torch.rand(adj.size(1), device=device, requires_grad=True)
    grad = torch.randn_like(x)

    with pytest.raises((AssertionError, NotImplementedError)):
        out = _TorchSPMM.apply(adj, x, value1, reduce, transpose)
        out.backward(grad)


def test_matmul_forward():
    x = torch.randn(3, 1)
    adj1 = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row')
    adj1_dense = adj1.to_dense()
    adj2 = EdgeIndex([[1, 0, 2, 1], [0, 1, 1, 2]], sort_order='col')
    adj2_dense = adj2.to_dense()

    out = adj1 @ x
    assert torch.allclose(out, adj1_dense @ x)

    out = adj1.matmul(x)
    assert torch.allclose(out, adj1_dense @ x)

    out = torch.matmul(adj1, x)
    assert torch.allclose(out, adj1_dense @ x)

    if torch_geometric.typing.WITH_PT20:
        out = torch.sparse.mm(adj1, x, reduce='sum')
    else:
        with pytest.raises(TypeError, match="got an unexpected keyword"):
            torch.sparse.mm(adj1, x, reduce='sum')
        out = torch.sparse.mm(adj1, x)
    assert torch.allclose(out, adj1_dense @ x)

    out, value = adj1 @ adj1
    assert isinstance(out, EdgeIndex)
    assert out.is_sorted_by_row
    assert out._sparse_size == (3, 3)
    assert out._rowptr is not None
    assert torch.allclose(out.to_dense(value), adj1_dense @ adj1_dense)

    out, value = adj1 @ adj2
    assert isinstance(out, EdgeIndex)
    assert torch.allclose(out.to_dense(value), adj1_dense @ adj2_dense)

    out, value = adj2 @ adj1
    assert isinstance(out, EdgeIndex)
    assert torch.allclose(out.to_dense(value), adj2_dense @ adj1_dense)

    out, value = adj2 @ adj2
    assert isinstance(out, EdgeIndex)
    assert torch.allclose(out.to_dense(value), adj2_dense @ adj2_dense)


def test_matmul_input_value():
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row')

    x = torch.randn(3, 1)
    value = torch.randn(4)

    out = adj.matmul(x, input_value=value)
    assert torch.allclose(out, adj.to_dense(value) @ x)


def test_matmul_backward():
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row')

    x1 = torch.randn(3, 1, requires_grad=True)
    value = torch.randn(4)

    out = adj.matmul(x1, input_value=value)
    grad_out = torch.randn_like(out)
    out.backward(grad_out)

    x2 = x1.detach().requires_grad_()
    dense_adj = adj.to_dense(value)
    out = dense_adj @ x2
    out.backward(grad_out)

    assert torch.allclose(x1.grad, x2.grad)


@withCUDA
@pytest.mark.parametrize('dtype', DTYPES)
def test_save_and_load(dtype, device, tmp_path):
    kwargs = dict(dtype=dtype, device=device)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    adj.fill_cache_()

    assert adj.sort_order == 'row'
    assert adj._indptr is not None

    path = osp.join(tmp_path, 'edge_index.pt')
    torch.save(adj, path)
    out = torch.load(path)

    assert isinstance(out, EdgeIndex)
    assert out.equal(adj)
    assert out.sort_order == 'row'
    assert out._indptr.equal(adj._indptr)


@pytest.mark.parametrize('dtype', DTYPES)
@pytest.mark.parametrize('num_workers', [0, 2])
def test_data_loader(dtype, num_workers):
    kwargs = dict(dtype=dtype)
    adj = EdgeIndex([[0, 1, 1, 2], [1, 0, 2, 1]], sort_order='row', **kwargs)
    adj.fill_cache_()

    loader = torch.utils.data.DataLoader(
        [adj] * 4,
        batch_size=2,
        num_workers=num_workers,
        collate_fn=lambda x: x,
        drop_last=True,
    )

    assert len(loader) == 2
    for batch in loader:
        assert isinstance(batch, list)
        assert len(batch) == 2
        for adj in batch:
            assert isinstance(adj, EdgeIndex)
            assert adj.dtype == adj.dtype
            assert adj.is_shared() == (num_workers > 0)
            assert adj._indptr.is_shared() == (num_workers > 0)


def test_torch_script():
    class Model(torch.nn.Module):
        def forward(self, x: Tensor, edge_index: EdgeIndex) -> Tensor:
            row, col = edge_index[0], edge_index[1]
            x_j = x[row]
            out = scatter(x_j, col, dim_size=edge_index.num_cols)
            return out

    x = torch.randn(3, 8)
    # Test that `num_cols` gets picked up by making last node isolated.
    edge_index = EdgeIndex([[0, 1, 1, 2], [1, 0, 0, 1]], sparse_size=(3, 3))

    model = Model()
    expected = model(x, edge_index)
    assert expected.size() == (3, 8)

    # `torch.jit.script` does not support inheritance at the `Tensor` level :(
    with pytest.raises(RuntimeError, match="attribute or method 'num_cols'"):
        torch.jit.script(model)

    # A valid workaround is to treat `EdgeIndex` as a regular PyTorch tensor
    # whenever we are in script mode:
    class ScriptableModel(torch.nn.Module):
        def forward(self, x: Tensor, edge_index: EdgeIndex) -> Tensor:
            row, col = edge_index[0], edge_index[1]
            x_j = x[row]
            dim_size: Optional[int] = None
            if (not torch.jit.is_scripting()
                    and isinstance(edge_index, EdgeIndex)):
                dim_size = edge_index.num_cols
            out = scatter(x_j, col, dim_size=dim_size)
            return out

    script_model = torch.jit.script(ScriptableModel())
    out = script_model(x, edge_index)
    assert out.size() == (2, 8)
    assert torch.allclose(out, expected[:2])


@onlyLinux
@disableExtensions
@withPackage('torch>=2.1.0')
def test_compile():
    import torch._dynamo as dynamo

    class Model(torch.nn.Module):
        def forward(self, x: Tensor, edge_index: EdgeIndex) -> Tensor:
            row, col = edge_index[0], edge_index[1]
            x_j = x[row]
            out = scatter(x_j, col, dim_size=edge_index.num_cols)
            return out

    x = torch.randn(3, 8)
    # Test that `num_cols` gets picked up by making last node isolated.
    edge_index = EdgeIndex([[0, 1, 1, 2], [1, 0, 0, 1]], sparse_size=(3, 3))

    model = Model()
    expected = model(x, edge_index)
    assert expected.size() == (3, 8)

    explanation = dynamo.explain(model)(x, edge_index)
    assert explanation.graph_break_count <= 0

    compiled_model = torch_geometric.compile(model)
    out = compiled_model(x, edge_index)
    assert torch.allclose(out, expected)


if __name__ == '__main__':
    import argparse

    warnings.filterwarnings('ignore', ".*Sparse CSR tensor support.*")

    parser = argparse.ArgumentParser()
    parser.add_argument('--device', type=str, default='cuda')
    parser.add_argument('--backward', action='store_true')
    args = parser.parse_args()

    channels = 128
    num_nodes = 20_000
    num_edges = 200_000

    x = torch.randn(num_nodes, channels, device=args.device)
    edge_index = EdgeIndex(
        torch.randint(0, num_nodes, size=(2, num_edges), device=args.device),
        sparse_size=(num_nodes, num_nodes),
    ).sort_by('row')[0]
    edge_index.fill_cache_()
    adj1 = edge_index.to_sparse_csr()
    adj2 = SparseTensor(
        row=edge_index[0],
        col=edge_index[1],
        sparse_sizes=(num_nodes, num_nodes),
    )

    def edge_index_mm(edge_index, x, reduce):
        return edge_index.matmul(x, reduce=reduce)

    def torch_sparse_mm(adj, x):
        return adj @ x

    def sparse_tensor_mm(adj, x, reduce):
        return adj.matmul(x, reduce=reduce)

    def scatter_mm(edge_index, x, reduce):
        return scatter(x[edge_index[1]], edge_index[0], dim_size=x.size(0),
                       reduce=reduce)

    funcs = [edge_index_mm, torch_sparse_mm, sparse_tensor_mm, scatter_mm]
    func_names = ['edge_index', 'torch.sparse', 'SparseTensor', 'scatter']

    for reduce in ReduceType.__args__:
        func_args = [(edge_index, x, reduce), (adj1, x), (adj2, x, reduce),
                     (edge_index, x, reduce)]
        print(f"reduce='{reduce}':")

        benchmark(
            funcs=funcs,
            func_names=func_names,
            args=func_args,
            num_steps=100 if args.device == 'cpu' else 1000,
            num_warmups=50 if args.device == 'cpu' else 500,
            backward=args.backward,
        )
