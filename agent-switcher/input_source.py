"""Temporarily switch macOS to an ASCII keyboard so j/k bypass Chinese IME composition."""
import ctypes
import os
import sys

_UTF8=0x08000100

def _load():
    if sys.platform!='darwin' or os.environ.get('SSH_CONNECTION'): return None
    try:
        cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        hi=ctypes.CDLL('/System/Library/Frameworks/Carbon.framework/Frameworks/HIToolbox.framework/HIToolbox')
    except OSError:
        return None
    vp=ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes=[vp,ctypes.c_char_p,ctypes.c_uint32]; cf.CFStringCreateWithCString.restype=vp
    cf.CFStringGetCString.argtypes=[vp,ctypes.c_char_p,ctypes.c_long,ctypes.c_uint32]; cf.CFStringGetCString.restype=ctypes.c_bool
    cf.CFDictionaryCreate.argtypes=[vp,ctypes.POINTER(vp),ctypes.POINTER(vp),ctypes.c_long,vp,vp]; cf.CFDictionaryCreate.restype=vp
    cf.CFArrayGetCount.argtypes=[vp]; cf.CFArrayGetCount.restype=ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes=[vp,ctypes.c_long]; cf.CFArrayGetValueAtIndex.restype=vp
    cf.CFRelease.argtypes=[vp]
    hi.TISCopyCurrentKeyboardInputSource.restype=vp
    hi.TISGetInputSourceProperty.argtypes=[vp,vp]; hi.TISGetInputSourceProperty.restype=vp
    hi.TISCreateInputSourceList.argtypes=[vp,ctypes.c_bool]; hi.TISCreateInputSourceList.restype=vp
    hi.TISSelectInputSource.argtypes=[vp]; hi.TISSelectInputSource.restype=ctypes.c_int32
    return cf,hi,vp.in_dll(hi,'kTISPropertyInputSourceID')

def current():
    api=_load()
    if not api: return None
    cf,hi,prop=api
    src=hi.TISCopyCurrentKeyboardInputSource()
    if not src: return None
    buf=ctypes.create_string_buffer(256)
    ok=cf.CFStringGetCString(hi.TISGetInputSourceProperty(src,prop),buf,256,_UTF8)
    cf.CFRelease(src)
    return buf.value.decode() if ok else None

def select(source_id):
    api=_load()
    if not api or not source_id: return False
    cf,hi,prop=api
    key=ctypes.c_void_p(prop.value)
    value=ctypes.c_void_p(cf.CFStringCreateWithCString(None,source_id.encode(),_UTF8))
    # Standard CFType callbacks so the dictionary retains and compares CFStrings correctly.
    filt=cf.CFDictionaryCreate(None,ctypes.byref(key),ctypes.byref(value),1,
        ctypes.addressof(ctypes.c_void_p.in_dll(cf,'kCFTypeDictionaryKeyCallBacks')),
        ctypes.addressof(ctypes.c_void_p.in_dll(cf,'kCFTypeDictionaryValueCallBacks')))
    sources=hi.TISCreateInputSourceList(filt,False)
    cf.CFRelease(filt); cf.CFRelease(value)
    if not sources: return False
    ok=cf.CFArrayGetCount(sources)>0 and hi.TISSelectInputSource(cf.CFArrayGetValueAtIndex(sources,0))==0
    cf.CFRelease(sources)
    return ok

ASCII=('com.apple.keylayout.ABC','com.apple.keylayout.US')

class AsciiInput:
    """Context manager: switch to an ASCII layout on enter, restore the previous source on exit."""
    def __enter__(self):
        self.previous=None
        try:
            before=current()
            if before and before not in ASCII and any(select(s) for s in ASCII): self.previous=before
        except (OSError,ValueError,AttributeError):
            pass
        return self
    def __exit__(self,*exc):
        try:
            if self.previous: select(self.previous)
        except (OSError,ValueError,AttributeError):
            pass
